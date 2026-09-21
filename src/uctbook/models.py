from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Item:
    """A bookable seat or room."""

    id: int
    label: str = ""
    location: int = 0
    category: int = 0
    zone: int = 0


@dataclass(frozen=True)
class Slot:
    """One grid cell.

    seat_id is the individual PC or chair, eid the resource it belongs to. The
    submit payload needs both. raw_start and raw_end are echoed back verbatim.
    """

    seat_id: int
    eid: int
    start: datetime
    end: datetime
    checksum: str
    free: bool
    raw_start: str
    raw_end: str

    @property
    def key(self) -> int:
        """What identifies the thing being booked. Seats carry their own id;
        a whole room reports seat 0 and is identified by its resource id."""
        return self.seat_id or self.eid


@dataclass(frozen=True)
class Run:
    """Consecutive free slots on one resource."""

    key: int
    slots: tuple[Slot, ...]

    @property
    def start(self) -> datetime:
        return self.slots[0].start

    @property
    def end(self) -> datetime:
        return self.slots[-1].end

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class Block:
    """A candidate booking: a contiguous stretch of one run."""

    key: int
    slots: tuple[Slot, ...]

    @property
    def seat_id(self) -> int:
        return self.slots[0].seat_id

    @property
    def eid(self) -> int:
        return self.slots[0].eid

    @property
    def start(self) -> datetime:
        return self.slots[0].start

    @property
    def end(self) -> datetime:
        return self.slots[-1].end

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class HeldBooking:
    ref: str  # target|date|start|resource, the idempotency handle
    target: str
    day: date
    start: datetime
    end: datetime
    key: int
    booking_id: str = ""
    cancel_token: str = ""
    checkin_code: str = ""
    status: str = "held"
    last_seen_on_server: str = ""


@dataclass
class BookingResult:
    success: bool
    block: Block
    target: str
    booking_id: str = ""
    message: str = ""


@dataclass
class GridPage:
    slots: list[Slot] = field(default_factory=list)
    bookings: list[dict[str, object]] = field(default_factory=list)
    window_end: bool = False
