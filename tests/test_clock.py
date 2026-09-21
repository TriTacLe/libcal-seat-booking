import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from uctbook.clock import fmt_libcal, parse_libcal, today
from uctbook.planner import window_edge

SAST = ZoneInfo("Africa/Johannesburg")
OSLO = ZoneInfo("Europe/Oslo")
SOURCE = Path(__file__).parent.parent / "src" / "uctbook"


def test_libcal_strings_round_trip():
    parsed = parse_libcal("2026-10-05 16:30:00", SAST)
    assert parsed.tzinfo is SAST
    assert fmt_libcal(parsed) == "2026-10-05 16:30:00"


def test_south_africa_has_no_dst():
    """The whole booking calendar leans on this, so assert it rather than assume."""
    for month in range(1, 13):
        moment = datetime(2026, month, 15, 12, tzinfo=SAST)
        assert moment.utcoffset() == timedelta(hours=2)


def test_the_edge_day_is_a_library_day_not_an_oslo_one():
    # 23:30 in Oslo in winter is already the next day in Cape Town.
    late_in_oslo = datetime(2026, 11, 15, 23, 30, tzinfo=OSLO)
    in_sast = late_in_oslo.astimezone(SAST)
    assert in_sast.date() == date(2026, 11, 16)
    assert window_edge(in_sast.date(), 14) == date(2026, 11, 30)


def test_today_follows_the_configured_zone():
    assert today(SAST) == datetime.now(SAST).date()


def test_nothing_outside_clock_reads_the_wall_clock():
    """A bare date.today() somewhere else is how the midnight run books the
    wrong day for five months of the year."""
    offenders = []
    for path in SOURCE.glob("*.py"):
        if path.name == "clock.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\b(datetime\.now|date\.today|datetime\.utcnow)\s*\(", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"wall-clock read outside clock.py: {', '.join(offenders)}"
