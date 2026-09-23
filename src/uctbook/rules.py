"""Booking limits. Every limit is a value on a Rules object rather than a
constant, because the three UCT locations publish different ones.
"""

from __future__ import annotations

from datetime import time

from pydantic import BaseModel, field_validator, model_validator


class RuleViolation(Exception):
    pass


def _hhmm(value: str) -> time:
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"expected HH:MM, got '{value}'")
    return time(int(parts[0]), int(parts[1]))


class Rules(BaseModel):
    slot_minutes: int = 5
    window_days: int = 14
    max_minutes_per_day: int = 240
    earliest: str = "08:00"
    latest: str = "19:30"
    # Some categories refuse a second booking that starts too soon after the
    # last one: "this category has a limit of 3 hours between bookings".
    min_gap_minutes: int = 0
    # Some categories cap the count rather than the minutes.
    max_bookings_per_day: int | None = None
    # And some cap a single booking shorter than the daily total.
    max_minutes_per_booking: int | None = None

    @field_validator("earliest", "latest")
    @classmethod
    def check_time(cls, v: str) -> str:
        _hhmm(v)
        return v

    @model_validator(mode="after")
    def check_span(self) -> "Rules":
        if _hhmm(self.earliest) >= _hhmm(self.latest):
            raise ValueError(f"earliest {self.earliest} is not before latest {self.latest}")
        if self.max_minutes_per_day % self.slot_minutes:
            raise ValueError("max_minutes_per_day must be a multiple of slot_minutes")
        if self.window_days < 0:
            raise ValueError("window_days cannot be negative")
        return self

    @property
    def earliest_time(self) -> time:
        return _hhmm(self.earliest)

    @property
    def latest_time(self) -> time:
        return _hhmm(self.latest)
