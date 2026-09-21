import json
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from uctbook.libcal import GridQuery, LibCalError, _encode, parse_grid

SAST = ZoneInfo("Africa/Johannesburg")


def test_grid_body_matches_the_browser(fixture):
    """Field order and encoding are the canary for LibCal changing its API."""
    query = GridQuery(lid=5154, gid=33215, zone=7304, filters=[5005, 5006], page_size=18)
    body = _encode(query.body(date(2026, 10, 5), date(2026, 10, 6)))
    assert body == fixture("grid-request.txt").strip()


def test_parses_slots(fixture):
    page = parse_grid(json.loads(fixture("grid-kc-computers.json")), SAST)
    assert len(page.slots) == 414
    assert {s.seat_id for s in page.slots} == {165230, 165231, 165232}
    first = page.slots[0]
    assert first.start.tzinfo is SAST
    assert first.raw_start == "2026-10-05 08:00:00"
    assert len(first.checksum) == 32


def test_a_classname_means_not_bookable():
    payload = {
        "slots": [
            {
                "itemId": 1,
                "start": "2026-10-05 08:00:00",
                "end": "2026-10-05 08:05:00",
                "checksum": "x",
            },
            {
                "itemId": 1,
                "start": "2026-10-05 08:05:00",
                "end": "2026-10-05 08:10:00",
                "checksum": "y",
                "className": "s-lc-eq-checkout",
            },
        ]
    }
    slots = parse_grid(payload, SAST).slots
    assert [s.free for s in slots] == [True, False]


def test_fills_in_the_resource_id():
    payload = {
        "slots": [
            {"itemId": 165230, "start": "2026-10-05 08:00:00", "end": "2026-10-05 08:05:00"}
        ]
    }
    slots = parse_grid(payload, SAST, {165230: 131384}).slots
    assert slots[0].eid == 131384


def test_window_end(fixture):
    page = parse_grid(json.loads(fixture("grid-window-end.json")), SAST)
    assert page.window_end is True
    assert page.slots == []


def test_malformed_slot_is_a_libcal_error():
    with pytest.raises(LibCalError):
        parse_grid({"slots": [{"itemId": "nope"}]}, SAST)
    with pytest.raises(LibCalError):
        parse_grid({"error": "maintenance"}, SAST)
