import json
from datetime import date, datetime
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


def test_room_categories_ask_for_the_room_not_a_seat():
    """seat=1 on a room category is rejected with "Seat not found"."""
    rooms = GridQuery(lid=5154, gid=13236, seat_booking=False)
    body = _encode(rooms.body(date(2026, 9, 28), date(2026, 9, 29)))
    assert "seat=0" in body
    seats = GridQuery(lid=5154, gid=33215)
    assert "seat=1" in _encode(seats.body(date(2026, 9, 28), date(2026, 9, 29)))


def test_booking_payload_shape():
    """One JSON entry per five-minute slot, times echoed back as LibCal sent them."""
    from uctbook.libcal import bookings_json
    from uctbook.models import Block, Slot

    def slot(start, end, checksum):
        return Slot(
            seat_id=165248, eid=131384,
            start=datetime(2026, 9, 22, tzinfo=SAST), end=datetime(2026, 9, 22, tzinfo=SAST),
            checksum=checksum, free=True, raw_start=start, raw_end=end,
        )

    block = Block(key=165248, slots=(
        slot("2026-09-22 09:00:00", "2026-09-22 09:05:00", "aaa"),
        slot("2026-09-22 09:05:00", "2026-09-22 09:10:00", "bbb"),
    ))
    assert json.loads(bookings_json(block, 5154)) == [
        {"lid": 5154, "eid": 131384, "seat_id": 165248,
         "start": "2026-09-22 09:00:00", "end": "2026-09-22 09:05:00", "checksum": "aaa"},
        {"lid": 5154, "eid": 131384, "seat_id": 165248,
         "start": "2026-09-22 09:05:00", "end": "2026-09-22 09:10:00", "checksum": "bbb"},
    ]
