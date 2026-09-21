"""The screen-reader booking flow at /r/accessible.

It renders every bookable 5-minute slot as a checkbox carrying exactly the
fields the submit payload wants: the resource (eid), the seat, the times and
the per-slot checksum. No JavaScript, no grid maths. It only shows 20 resources
per view, so the JSON grid stays the reader for wide categories, but this page
is where the seat-to-resource mapping and the payload shape come from.
"""

from __future__ import annotations

import re
from datetime import date
from zoneinfo import ZoneInfo

import httpx

from uctbook.clock import parse_libcal
from uctbook.models import Slot

HOST = "uct.ac.za.libcal.com"
AVAILABILITY_URL = f"https://{HOST}/r/accessible/availability"

_CHECKBOX_RE = re.compile(
    r'data-eid="(?P<eid>\d+)"\s*'
    r'data-seat="(?P<seat>\d+)"\s*'
    r'data-start="(?P<start>[^"]+)"\s*'
    r'data-end="(?P<end>[^"]+)"\s*'
    r'data-crc="(?P<crc>[0-9a-f]+)"',
    re.S,
)
_PANEL_RE = re.compile(
    r'<h2 class="panel-title">\s*(?P<label>[^<\n]+?)\s*\n.*?id="slots-(?P<resource>\d+)"',
    re.S,
)


def parse_slots(html: str, tz: ZoneInfo) -> list[Slot]:
    """Every checkbox on this page is a bookable slot; taken ones are not rendered."""
    slots: list[Slot] = []
    for m in _CHECKBOX_RE.finditer(html):
        slots.append(
            Slot(
                seat_id=int(m["seat"]),
                eid=int(m["eid"]),
                start=parse_libcal(m["start"], tz),
                end=parse_libcal(m["end"], tz),
                checksum=m["crc"],
                free=True,
                raw_start=m["start"],
                raw_end=m["end"],
            )
        )
    return slots


def parse_labels(html: str) -> dict[int, str]:
    """Resource key to its printed name. The key is the seat for seat bookings
    and the resource id for whole rooms, matching the slots-NNN panel id."""
    return {int(m["resource"]): m["label"] for m in _PANEL_RE.finditer(html)}


def parse_resources(html: str) -> dict[int, int]:
    """Resource key to the eid the submit payload needs."""
    return {
        (int(m["seat"]) or int(m["eid"])): int(m["eid"]) for m in _CHECKBOX_RE.finditer(html)
    }


def fetch(
    lid: int,
    gid: int,
    zone: int,
    capacity: int,
    day: date,
    client: httpx.Client | None = None,
) -> str:
    own = client is None
    client = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        resp = client.get(
            AVAILABILITY_URL,
            params={
                "lid": lid,
                "zone": zone,
                "gid": gid,
                "capacity": capacity,
                "space": 0,
                "date": day.isoformat(),
            },
        )
        resp.raise_for_status()
        return resp.text
    finally:
        if own:
            client.close()
