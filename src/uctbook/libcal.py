"""LibCal client.

Reading availability needs no session, so it runs over plain httpx. The booking
calls need the SSO session and will run inside the authenticated Playwright page
instead; their payloads get captured by 'uct recon' before they are written.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from uctbook.clock import parse_libcal
from uctbook.models import Block, GridPage, Slot

logger = logging.getLogger(__name__)

HOST = "uct.ac.za.libcal.com"
GRID_URL = f"https://{HOST}/spaces/availability/grid"

# The grid rejects a request whose Referer is not a LibCal page.
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": f"https://{HOST}",
    "Referer": f"https://{HOST}/r/new/availability",
}


class LibCalError(Exception):
    pass


class GridQuery:
    """The form body LibCal expects. Field order is kept as the site sends it,
    so the request can be diffed against a real capture."""

    def __init__(
        self,
        lid: int,
        gid: int = 0,
        zone: int = 0,
        capacity: int = -1,
        filters: list[int] | None = None,
        page_size: int = 200,
        seat_booking: bool = True,
    ) -> None:
        self.lid = lid
        self.gid = gid
        self.zone = zone
        self.capacity = capacity
        self.filters = filters or []
        self.page_size = page_size
        self.seat_booking = seat_booking

    def body(self, start: date, end: date, page_index: int = 0) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = [
            ("lid", str(self.lid)),
            ("gid", str(self.gid)),
            ("eid", "-1"),
            ("seat", "1" if self.seat_booking else "0"),
            ("seatId", "0"),
            ("zone", str(self.zone)),
        ]
        fields += [("filters[]", str(f)) for f in self.filters]
        fields += [
            ("start", start.isoformat()),
            ("end", end.isoformat()),
            ("pageIndex", str(page_index)),
            ("pageSize", str(self.page_size)),
        ]
        return fields


def parse_grid(
    payload: dict[str, Any], tz: ZoneInfo, eid_by_seat: dict[int, int] | None = None
) -> GridPage:
    """itemId in the grid is the seat, not the resource. The resource id comes
    from the accessible page and is needed to submit."""
    eid_by_seat = eid_by_seat or {}
    raw_slots = payload.get("slots")
    if raw_slots is None:
        raise LibCalError(f"Grid response has no 'slots' key: {list(payload)[:5]}")

    slots: list[Slot] = []
    for raw in raw_slots:
        try:
            seat_id = int(raw["itemId"])
            slots.append(
                Slot(
                    seat_id=seat_id,
                    eid=eid_by_seat.get(seat_id, 0),
                    start=parse_libcal(raw["start"], tz),
                    end=parse_libcal(raw["end"], tz),
                    checksum=str(raw.get("checksum", "")),
                    # A className marks a state: taken, padding, already checked out.
                    # A bare slot is the bookable one.
                    free=not raw.get("className"),
                    raw_start=raw["start"],
                    raw_end=raw["end"],
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LibCalError(f"Malformed slot {raw!r}: {e}") from e

    return GridPage(
        slots=slots,
        bookings=list(payload.get("bookings") or []),
        window_end=bool(payload.get("windowEnd")),
    )


class ReadClient:
    def __init__(
        self,
        tz: ZoneInfo,
        client: httpx.Client | None = None,
        eid_by_seat: dict[int, int] | None = None,
    ) -> None:
        self.tz = tz
        self.eid_by_seat = eid_by_seat or {}
        self._own = client is None
        self.http = client or httpx.Client(timeout=30.0, follow_redirects=True, headers=HEADERS)

    def close(self) -> None:
        if self._own:
            self.http.close()

    def grid(self, query: GridQuery, day: date) -> GridPage:
        body = query.body(day, day + timedelta(days=1))
        resp = self.http.post(GRID_URL, content=_encode(body), headers=HEADERS)
        if resp.status_code != 200:
            raise LibCalError(f"Grid returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise LibCalError(f"Grid returned non-JSON: {resp.text[:200]}") from e
        page = parse_grid(payload, self.tz, self.eid_by_seat)
        logger.debug("grid %s lid=%s gid=%s: %d slots", day, query.lid, query.gid, len(page.slots))
        return page


def _encode(fields: list[tuple[str, str]]) -> str:
    from urllib.parse import quote_plus

    return "&".join(f"{quote_plus(k)}={quote_plus(v)}" for k, v in fields)


TIMES_URL = f"https://{HOST}/ajax/space/times"
BOOK_URL = f"https://{HOST}/ajax/space/book"

# LibCal holds the slots for about five minutes between these two calls.
_SESSION_RE = re.compile(r'name="session"\s+value="(\d+)"')
_CANCEL_RE = re.compile(r'/equipment/cancel\?id=([0-9a-f]+)')
_BOOKING_RE = re.compile(r'Booking_(\d+)\.ics|booking[_-]?id["\s:=]+(\d+)', re.I)
_CHECKIN_RE = re.compile(r'(?:code|Code)[^<]{0,20}?<strong>([A-Z0-9]{3,8})</strong>')


@dataclass
class Confirmation:
    html: str
    booking_id: str = ""
    cancel_token: str = ""
    checkin_code: str = ""


def parse_confirmation(html: str) -> Confirmation:
    """Pull out whatever the confirmation page gives us. LibCal mails the
    check-in code rather than showing it, so that one is often empty."""
    cancel = _CANCEL_RE.search(html)
    booking = _BOOKING_RE.search(html)
    checkin = _CHECKIN_RE.search(html)
    return Confirmation(
        html=html,
        booking_id=(booking.group(1) or booking.group(2)) if booking else "",
        cancel_token=cancel.group(1) if cancel else "",
        checkin_code=checkin.group(1) if checkin else "",
    )
_ACTION_RE = re.compile(r'id="s-lc-eq-bform"[^>]*action="([^"]+)"')


class HoldRejected(LibCalError):
    """The slots went to someone else, or a checksum went stale."""


class CategoryFull(LibCalError):
    """This category has nothing more to give today: the daily cap is used up,
    the per-day count is reached, or the next slot is inside its required gap.

    Worth telling apart from a generic failure, because retrying it is wasted
    load on the library's server and the answer will not change today.
    """


@dataclass
class Hold:
    session: str
    action: str
    html: str


def _bookings_fields(block: Block, lid: int) -> list[tuple[str, str]]:
    """One entry per five-minute slot, each with its own checksum. The times
    are echoed back exactly as LibCal sent them."""
    fields: list[tuple[str, str]] = []
    for i, slot in enumerate(block.slots):
        fields += [
            (f"bookings[{i}][lid]", str(lid)),
            (f"bookings[{i}][eid]", str(slot.eid)),
            (f"bookings[{i}][seat_id]", str(slot.seat_id)),
            (f"bookings[{i}][start]", slot.raw_start),
            (f"bookings[{i}][end]", slot.raw_end),
            (f"bookings[{i}][checksum]", slot.checksum),
        ]
    return fields


def bookings_json(block: Block, lid: int) -> str:
    return json.dumps(
        [
            {
                "lid": lid,
                "eid": slot.eid,
                "seat_id": slot.seat_id,
                "start": slot.raw_start,
                "end": slot.raw_end,
                "checksum": slot.checksum,
            }
            for slot in block.slots
        ],
        separators=(",", ":"),
    )


class BookingClient:
    """The two calls that make a booking.

    No browser and no login: LibCal gates seat booking on a UCT email address,
    not on a session. Availability, hold and submit all work anonymously.
    """

    def __init__(self, tz: ZoneInfo, client: httpx.Client | None = None) -> None:
        self.tz = tz
        self._own = client is None
        self.http = client or httpx.Client(timeout=60.0, follow_redirects=True)

    def close(self) -> None:
        if self._own:
            self.http.close()

    def hold(self, block: Block, lid: int, gid: int, zone: int, capacity: int = -1) -> Hold:
        """Reserve the slots for a few minutes and get the booking form back."""
        return_url = f"/r/accessible?lid={lid}&gid={gid}&zone={zone}&space=0&capacity={capacity}"
        fields = [("returnUrl", return_url)]
        fields += _bookings_fields(block, lid)
        fields.append(("method", "14"))

        headers = dict(HEADERS)
        headers["Referer"] = f"https://{HOST}{return_url}"
        resp = self.http.post(TIMES_URL, content=_encode(fields), headers=headers)
        if resp.status_code != 200:
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.text).split())[:300]
            if _is_full(text):
                raise CategoryFull(text)
            raise LibCalError(f"Hold returned HTTP {resp.status_code}: {text[:200]}")

        payload = resp.json()
        if payload.get("redirect"):
            raise LibCalError(f"LibCal wants a login first: {payload['redirect']}")
        html = str(payload.get("html", ""))
        if not html:
            raise HoldRejected(f"Hold refused: {str(payload)[:300]}")

        session = _SESSION_RE.search(html)
        action = _ACTION_RE.search(html)
        if not session:
            raise HoldRejected(f"No booking session in the response: {html[:300]}")
        return Hold(
            session=session.group(1),
            action=action.group(1) if action else "/ajax/space/book",
            html=html,
        )

    def submit(
        self, hold: Hold, block: Block, lid: int, patron: dict[str, str | list[str]]
    ) -> Confirmation:
        """Complete the booking."""
        fields: list[tuple[str, str]] = [("session", hold.session)]
        for name, value in patron.items():
            # A consent can be several checkboxes sharing one name.
            for one in value if isinstance(value, list) else [value]:
                fields.append((name, one))
        fields.append(("bookings", bookings_json(block, lid)))
        fields.append(("returnUrl", f"/r/accessible?lid={lid}"))
        fields.append(("method", "14"))

        # The form posts multipart, and LibCal's parser expects that shape.
        # It also refuses a submit whose Referer is not a LibCal page.
        parts = [(name, (None, value)) for name, value in fields]
        resp = self.http.post(
            f"https://{HOST}{hold.action}",
            files=parts,
            headers={
                "Referer": f"https://{HOST}/r/accessible?lid={lid}",
                "Origin": f"https://{HOST}",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if resp.status_code != 200:
            raise LibCalError(f"Booking returned HTTP {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()
        error = payload.get("error")
        if error:
            text = " ".join(re.sub(r"<[^>]+>", " ", str(error)).split())[:300]
            if _is_full(text):
                raise CategoryFull(text)
            raise LibCalError(text)
        return parse_confirmation(str(payload.get("html", "")))


def cancel(token: str, client: httpx.Client) -> bool:
    """Cancel a confirmed booking with the token from its confirmation.

    The token is the whole authorisation, so it is a credential: anyone holding
    it can cancel the booking.
    """
    resp = client.get(f"https://{HOST}/equipment/cancel", params={"id": token})
    return resp.status_code == 200


FULL_PHRASES = (
    "per day limit",
    "limit of",
    "hours between bookings",
    "exceeds the",
)


def _is_full(message: str) -> bool:
    low = message.lower()
    return any(phrase in low for phrase in FULL_PHRASES)


def check_in(code: str, client: httpx.Client) -> bool:
    """Check in with the code from the confirmation mail."""
    resp = client.post(
        f"https://{HOST}/r/checkin",
        data={"code": code},
        headers={"Referer": f"https://{HOST}/r/checkin", "X-Requested-With": "XMLHttpRequest"},
    )
    if resp.status_code != 200:
        return False
    body = resp.text.lower()
    return "error" not in body and "invalid" not in body


def release(hold: Hold, client: httpx.Client) -> None:
    """Give the slots back rather than letting the hold time out."""
    try:
        client.post(f"https://{HOST}/ajax/space/session/end", data={"session": hold.session})
    except httpx.HTTPError:
        pass
