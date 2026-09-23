from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from uctbook.config import TargetSpec
from uctbook.models import HeldBooking, Slot
from uctbook.planner import choose, minutes_used, runs, window_edge

SAST = ZoneInfo("Africa/Johannesburg")
DAY = date(2026, 10, 5)  # a Monday


def slots(seat, start_hhmm, count, *, eid=131384, free=True, gap_after=None):
    hour, minute = (int(x) for x in start_hhmm.split(":"))
    cursor = datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=SAST)
    out = []
    for i in range(count):
        end = cursor + timedelta(minutes=5)
        out.append(
            Slot(
                seat_id=seat,
                eid=eid,
                start=cursor,
                end=end,
                checksum=f"{seat}-{i}",
                free=free,
                raw_start=cursor.strftime("%Y-%m-%d %H:%M:%S"),
                raw_end=end.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        cursor = end
        if gap_after is not None and i == gap_after:
            cursor += timedelta(minutes=5)
    return out


def spec(**want):
    base = {"minutes": 60, "min_minutes": 30, "step_minutes": 15}
    base.update(want)
    return TargetSpec(name="t", location="L", want=base)


def test_a_gap_ends_a_run():
    found = runs(slots(1, "09:00", 6, gap_after=2))
    assert [r.minutes for r in found] == [15, 15]


def test_taken_slots_are_not_in_a_run():
    found = runs(slots(1, "09:00", 4) + slots(2, "09:00", 4, free=False))
    assert [r.key for r in found] == [1]


def test_rooms_group_by_resource_when_there_is_no_seat():
    found = runs(slots(0, "09:00", 4, eid=50959))
    assert [r.key for r in found] == [50959]


def test_picks_the_asked_for_length():
    block = choose(slots(1, "09:00", 24), spec(), DAY, [])
    assert block is not None
    assert block.minutes == 60
    assert block.start.strftime("%H:%M") == "09:00"


def test_shrinks_rather_than_giving_up():
    block = choose(slots(1, "09:00", 9), spec(minutes=60, min_minutes=30), DAY, [])
    assert block is not None
    assert block.minutes == 45


def test_refuses_a_stub_below_the_floor():
    assert choose(slots(1, "09:00", 3), spec(minutes=60, min_minutes=30), DAY, []) is None


def test_prefers_the_named_seat():
    all_slots = slots(1, "09:00", 24) + slots(2, "09:00", 24)
    target = spec()
    target.items.prefer = ["KC02"]
    block = choose(all_slots, target, DAY, [], {1: "KC01", 2: "KC02"})
    assert block is not None and block.key == 2


def test_skips_an_excluded_seat():
    all_slots = slots(1, "09:00", 24) + slots(2, "09:00", 24)
    target = spec()
    target.items.exclude = ["KC01"]
    block = choose(all_slots, target, DAY, [], {1: "KC01", 2: "KC02"})
    assert block is not None and block.key == 2


def test_same_input_picks_the_same_seat_twice():
    all_slots = slots(2, "09:00", 24) + slots(1, "09:00", 24)
    first = choose(all_slots, spec(), DAY, [])
    second = choose(list(reversed(all_slots)), spec(), DAY, [])
    assert first is not None and second is not None
    assert first.key == second.key


def held(start_hhmm, minutes):
    hour, minute = (int(x) for x in start_hhmm.split(":"))
    start = datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=SAST)
    return HeldBooking(
        ref="k",
        target="t",
        day=DAY,
        start=start,
        end=start + timedelta(minutes=minutes),
        key=1,
    )


def test_does_not_double_book_the_same_hour():
    """The held hour is skipped, the free one after it is taken."""
    target = spec(minutes=60, min_minutes=60)
    block = choose(slots(1, "09:00", 24), target, DAY, [held("09:00", 60)])
    assert block is not None
    assert block.start.strftime("%H:%M") == "10:00"


def test_nothing_left_once_the_free_time_is_held():
    target = spec(minutes=60, min_minutes=60)
    block = choose(slots(1, "09:00", 12), target, DAY, [held("09:00", 60)])
    assert block is None


def test_a_partly_used_day_keeps_its_headroom():
    target = spec(minutes=240, min_minutes=60)
    target.rules.max_minutes_per_day = 240
    block = choose(slots(1, "13:00", 48), target, DAY, [held("09:00", 120)])
    assert block is not None
    assert block.minutes == 120


def test_a_full_day_books_nothing():
    target = spec(minutes=60, min_minutes=60)
    target.rules.max_minutes_per_day = 240
    assert choose(slots(1, "13:00", 48), target, DAY, [held("09:00", 240)]) is None


def test_stays_inside_the_wanted_window():
    target = spec(minutes=60, min_minutes=60)
    target.want.earliest_start = "10:00"
    target.want.latest_end = "11:00"
    block = choose(slots(1, "08:00", 48), target, DAY, [])
    assert block is not None
    assert block.start.strftime("%H:%M") == "10:00"
    assert block.end.strftime("%H:%M") == "11:00"


def test_skips_a_weekday_not_asked_for():
    target = spec()
    target.want.weekdays = ["sun"]
    assert choose(slots(1, "09:00", 24), target, DAY, []) is None


def test_minutes_used_ignores_other_days():
    other = held("09:00", 60)
    other.day = date(2026, 10, 6)
    assert minutes_used(DAY, [held("09:00", 60), other]) == 60


def test_window_edge():
    assert window_edge(date(2026, 9, 21), 14) == date(2026, 10, 5)


def test_another_category_keeps_its_own_quota():
    """The cap is per category, so a full day of seats still leaves rooms free."""
    seats = held("09:00", 240)
    seats.target = "kc-computers"
    target = spec(minutes=120, min_minutes=60)
    target.name = "kc-group-rooms"
    block = choose(slots(1, "13:00", 48), target, DAY, [seats])
    assert block is not None


def test_overlap_is_refused_then_allowed():
    """A different category may overlap once the free time has run out."""
    existing = held("09:00", 120)
    existing.target = "another-category"
    only = slots(1, "09:00", 24)
    assert choose(only, spec(minutes=120, min_minutes=120), DAY, [existing]) is None
    anyway = choose(only, spec(minutes=120, min_minutes=120), DAY, [existing], allow_overlap=True)
    assert anyway is not None


def test_a_category_that_wants_a_gap_gets_one():
    """Some categories refuse a booking within three hours of the last one."""
    existing = held("08:00", 180)
    existing.target = "t"
    target = spec(minutes=60, min_minutes=60)
    target.rules.min_gap_minutes = 180
    assert choose(slots(1, "11:00", 12), target, DAY, [existing]) is None
    assert choose(slots(1, "14:00", 12), target, DAY, [existing]) is not None


def test_never_overlaps_inside_one_category():
    """Across categories overlap is fine; inside one the server always says no."""
    existing = held("09:00", 120)
    existing.target = "t"
    target = spec(minutes=120, min_minutes=120)
    assert choose(slots(1, "09:00", 24), target, DAY, [existing], allow_overlap=True) is None
