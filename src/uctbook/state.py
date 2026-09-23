"""What has been booked.

LibCal stays the authority on what is allowed; this only stops the planner
proposing the same 08:00 block in every category because it cannot remember the
last one. Quota is per category, so the sums here are kept per target too.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from uctbook.models import Block, HeldBooking


def _ref(target: str, block: Block) -> str:
    return f"{target}|{block.start.date()}|{block.start.strftime('%H:%M')}|{block.key}"


class Ledger:
    def __init__(
        self,
        path: Path,
        bookings: list[HeldBooking] | None = None,
        full: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.bookings = bookings or []
        # "target|date" for combinations the server has already refused.
        self.full = full or {}

    @classmethod
    def load(cls, path: Path, tz: ZoneInfo) -> "Ledger":
        if not path.exists():
            return cls(path)
        raw = json.loads(path.read_text())
        bookings = []
        for row in raw.get("bookings", []):
            bookings.append(
                HeldBooking(
                    ref=row["ref"],
                    target=row["target"],
                    day=date.fromisoformat(row["day"]),
                    start=datetime.fromisoformat(row["start"]).replace(tzinfo=tz),
                    end=datetime.fromisoformat(row["end"]).replace(tzinfo=tz),
                    key=int(row["key"]),
                    booking_id=row.get("booking_id", ""),
                    cancel_token=row.get("cancel_token", ""),
                    checkin_code=row.get("checkin_code", ""),
                    status=row.get("status", "held"),
                )
            )
        return cls(path, bookings, raw.get('full', {}))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "version": 1,
            "full": self.full,
            "bookings": [
                {
                    "ref": b.ref,
                    "target": b.target,
                    "day": b.day.isoformat(),
                    "start": b.start.replace(tzinfo=None).isoformat(),
                    "end": b.end.replace(tzinfo=None).isoformat(),
                    "key": b.key,
                    "booking_id": b.booking_id,
                    "cancel_token": b.cancel_token,
                    "checkin_code": b.checkin_code,
                    "status": b.status,
                }
                for b in self.bookings
            ],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, self.path)

    def add(self, target: str, block: Block) -> HeldBooking:
        booking = HeldBooking(
            ref=_ref(target, block),
            target=target,
            day=block.start.date(),
            start=block.start,
            end=block.end,
            key=block.key,
        )
        self.bookings.append(booking)
        return booking

    def has(self, target: str, block: Block) -> bool:
        wanted = _ref(target, block)
        return any(b.ref == wanted for b in self.bookings)

    def for_day(self, day: date) -> list[HeldBooking]:
        return [b for b in self.bookings if b.day == day and b.status == "held"]

    def minutes(self, day: date, target: str) -> int:
        return sum(
            int((b.end - b.start).total_seconds() // 60)
            for b in self.for_day(day)
            if b.target == target
        )

    def count(self, day: date) -> int:
        return len(self.for_day(day))

    def mark_full(self, target: str, day: date, why: str = "") -> None:
        """Remember that LibCal said no more today, so the next run does not ask
        again. The answer will not change until the date does."""
        self.full[f"{target}|{day.isoformat()}"] = why
        self.save()

    def is_full(self, target: str, day: date) -> bool:
        return f"{target}|{day.isoformat()}" in self.full

    def live(self, now: datetime) -> list[HeldBooking]:
        """Bookings still held and not yet finished."""
        return [b for b in self.bookings if b.status == "held" and b.end > now]

    def due_checkin(self, now: datetime, offset_minutes: int) -> list[HeldBooking]:
        """Started, not yet checked in, and still inside the 15 minute window."""
        out = []
        for b in self.bookings:
            if b.status != "held" or not b.checkin_code:
                continue
            opens = b.start + timedelta(minutes=offset_minutes)
            if opens <= now < b.start + timedelta(minutes=14) and now < b.end:
                out.append(b)
        return out
