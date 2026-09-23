from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from uctbook.models import Block, Slot
from uctbook.state import Ledger

SAST = ZoneInfo("Africa/Johannesburg")
DAY = date(2026, 10, 5)


def block(seat, hour, minutes):
    start = datetime(2026, 10, 5, hour, tzinfo=SAST)
    slots = []
    for i in range(minutes // 5):
        s = start + timedelta(minutes=5 * i)
        e = s + timedelta(minutes=5)
        slots.append(Slot(seat, 131384, s, e, f"c{i}", True,
                          s.strftime("%Y-%m-%d %H:%M:%S"), e.strftime("%Y-%m-%d %H:%M:%S")))
    return Block(key=seat, slots=tuple(slots))


def test_quota_is_counted_per_category(tmp_path):
    led = Ledger(tmp_path / "s.json")
    led.add("kc-computers", block(1, 8, 240))
    assert led.minutes(DAY, "kc-computers") == 240
    assert led.minutes(DAY, "kc-group-rooms") == 0


def test_the_same_block_is_not_added_twice(tmp_path):
    led = Ledger(tmp_path / "s.json")
    b = block(1, 8, 120)
    led.add("kc-computers", b)
    assert led.has("kc-computers", b)
    assert not led.has("kc-group-rooms", b)


def test_round_trips_through_disk(tmp_path):
    path = tmp_path / "s.json"
    led = Ledger(path)
    led.add("kc-computers", block(1, 8, 120))
    led.save()
    again = Ledger.load(path, SAST)
    assert len(again.bookings) == 1
    assert again.minutes(DAY, "kc-computers") == 120
    assert again.bookings[0].start.tzinfo is SAST


def test_missing_file_is_an_empty_ledger(tmp_path):
    assert Ledger.load(tmp_path / "nothing.json", SAST).bookings == []


def test_counts_only_the_day_asked_for(tmp_path):
    led = Ledger(tmp_path / "s.json")
    led.add("kc-computers", block(1, 8, 120))
    assert led.count(DAY) == 1
    assert led.count(date(2026, 10, 6)) == 0
