from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from uctbook.models import HeldBooking
from uctbook.notifier import schedule

SAST = ZoneInfo("Africa/Johannesburg")


def booking(day, hour, minutes, key, target="t", code=""):
    start = datetime(2026, 10, day, hour, tzinfo=SAST)
    return HeldBooking(
        ref=f"{target}|{day}|{hour}|{key}",
        target=target,
        day=date(2026, 10, day),
        start=start,
        end=start + timedelta(minutes=minutes),
        key=key,
        checkin_code=code,
    )


def test_groups_by_day_and_totals_hours():
    out = schedule(
        [booking(5, 9, 120, 1), booking(5, 14, 60, 2), booking(6, 8, 60, 1)],
        {1: "KC01, Oppenheimer", 2: "Room 1, Oppenheimer"},
    )
    assert "Monday 05 October  (3.0h)" in out
    assert "Tuesday 06 October  (1.0h)" in out
    assert "09:00-11:00  KC01, Oppenheimer" in out
    assert "3 bookings, 4.0 hours in total." in out


def test_says_when_a_code_is_missing():
    out = schedule([booking(5, 9, 60, 1)], {1: "KC01"})
    assert "no code yet" in out
    with_code = schedule([booking(5, 9, 60, 1, code="Z2V")], {1: "KC01"})
    assert "code Z2V" in with_code


def test_unknown_resource_falls_back_to_its_id():
    assert "99" in schedule([booking(5, 9, 60, 99)], {})


def test_nothing_booked():
    assert schedule([], {}) == "Nothing booked."


def test_html_has_a_table_per_day_and_marks_missing_codes():
    from uctbook.notifier import as_html

    out = as_html(
        [booking(5, 9, 120, 1, code="2CG"), booking(6, 8, 60, 2)],
        {1: "KC01, Oppenheimer", 2: "Room 1, Snape"},
    )
    assert out.count("<table") == 2
    assert "Monday 05 October" in out
    assert "<strong style=\"font-size:16px;\">2CG</strong>" in out
    assert "code in its own mail" in out
    # the building belongs under the room, not glued to it
    assert "KC01<div" in out


def test_html_handles_a_place_with_no_building():
    from uctbook.notifier import as_html

    assert "KC01" in as_html([booking(5, 9, 60, 1)], {1: "KC01"})
