"""Every wall-clock read goes through here.

The tool runs on a server in Europe and books a library in South Africa, so a
bare date.today() silently picks the wrong day around midnight. LibCal sends
naive local timestamps; they are only meaningful against the library's zone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LIBCAL_FORMAT = "%Y-%m-%d %H:%M:%S"


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def parse_libcal(value: str, tz: ZoneInfo) -> datetime:
    return datetime.strptime(value, LIBCAL_FORMAT).replace(tzinfo=tz)


def fmt_libcal(value: datetime) -> str:
    return value.strftime(LIBCAL_FORMAT)


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def combine(day: date, moment: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, moment).replace(tzinfo=tz)


def days_from(day: date, count: int) -> date:
    return day + timedelta(days=count)


def utc_now() -> datetime:
    """For log lines and file names. Booking arithmetic uses the library zone."""
    return datetime.now(timezone.utc)


def stamp() -> str:
    return utc_now().strftime("%Y%m%d-%H%M%S")
