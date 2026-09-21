"""LibCal client.

Reading availability needs no session, so it runs over plain httpx. The booking
calls need the SSO session and will run inside the authenticated Playwright page
instead; their payloads get captured by 'uct recon' before they are written.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from uctbook.clock import parse_libcal
from uctbook.models import GridPage, Slot

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
    ) -> None:
        self.lid = lid
        self.gid = gid
        self.zone = zone
        self.capacity = capacity
        self.filters = filters or []
        self.page_size = page_size

    def body(self, start: date, end: date, page_index: int = 0) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = [
            ("lid", str(self.lid)),
            ("gid", str(self.gid)),
            ("eid", "-1"),
            ("seat", "1"),
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
