"""Turning 5-minute slots into a block worth booking.

Pure functions: no network, no browser, no files. Everything here is decided
from a parsed grid plus the bookings already held.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from uctbook.config import TargetSpec
from uctbook.models import Block, HeldBooking, Run, Slot


def runs(slots: list[Slot]) -> list[Run]:
    """Consecutive free slots, per seat. A single missing slot ends a run:
    LibCal refuses a selection with a gap in it."""
    grouped: dict[int, list[Slot]] = {}
    for slot in slots:
        if slot.free:
            grouped.setdefault(slot.key, []).append(slot)

    out: list[Run] = []
    for key, own in grouped.items():
        own.sort(key=lambda s: s.start)
        current: list[Slot] = []
        for slot in own:
            if current and slot.start != current[-1].end:
                out.append(Run(key=key, slots=tuple(current)))
                current = []
            current.append(slot)
        if current:
            out.append(Run(key=key, slots=tuple(current)))
    return out


def _clip(run: Run, window_start: datetime, window_end: datetime) -> list[Slot]:
    return [s for s in run.slots if s.start >= window_start and s.end <= window_end]


def candidates(
    all_runs: list[Run],
    spec: TargetSpec,
    window_start: datetime,
    window_end: datetime,
    minutes: int,
) -> list[Block]:
    """Every contiguous stretch of exactly `minutes` that fits the window."""
    slot_minutes = spec.rules.slot_minutes
    want_slots = minutes // slot_minutes
    step_slots = max(1, spec.want.step_minutes // slot_minutes)
    blocks: list[Block] = []

    for run in all_runs:
        usable = _clip(run, window_start, window_end)
        if len(usable) < want_slots:
            continue
        for offset in range(0, len(usable) - want_slots + 1, step_slots):
            chunk = tuple(usable[offset : offset + want_slots])
            blocks.append(Block(key=run.key, slots=chunk))
    return blocks


def _rank(block: Block, spec: TargetSpec, labels: dict[int, str]) -> tuple[int, int, datetime, int]:
    prefer = spec.items.prefer
    label = labels.get(block.key, "")
    rank = len(prefer)
    for i, wanted in enumerate(prefer):
        if wanted == str(block.key) or (label and wanted.casefold() == label.casefold()):
            rank = i
            break
    # longest first, then earliest, then a stable id so a re-run picks the same seat
    return (rank, -block.minutes, block.start, block.key)


def excluded(block: Block, spec: TargetSpec, labels: dict[int, str]) -> bool:
    label = labels.get(block.key, "")
    for unwanted in spec.items.exclude:
        if unwanted == str(block.key) or (label and unwanted.casefold() == label.casefold()):
            return True
    return False


def overlaps(block: Block, held: list[HeldBooking]) -> bool:
    return any(block.start < h.end and h.start < block.end for h in held)


def minutes_used(day: date, held: list[HeldBooking]) -> int:
    return sum(
        int((h.end - h.start).total_seconds() // 60) for h in held if h.day == day
    )


def choose(
    slots: list[Slot],
    spec: TargetSpec,
    day: date,
    held: list[HeldBooking],
    labels: dict[int, str] | None = None,
) -> Block | None:
    """Best block for one target on one day, or None when nothing fits.

    Shrinks towards min_minutes rather than giving up, but never books a stub
    below it: a 30-minute booking costs the same quota as a useful one.
    """
    labels = labels or {}
    if not spec.want.allows(day.weekday()):
        return None

    tz = slots[0].start.tzinfo if slots else None
    if tz is None:
        return None

    window_start = datetime.combine(day, spec.want.earliest_start_time).replace(tzinfo=tz)
    window_end = datetime.combine(day, spec.want.latest_end_time).replace(tzinfo=tz)
    rules_start = datetime.combine(day, spec.rules.earliest_time).replace(tzinfo=tz)
    rules_end = datetime.combine(day, spec.rules.latest_time).replace(tzinfo=tz)
    window_start = max(window_start, rules_start)
    window_end = min(window_end, rules_end)
    if window_start >= window_end:
        return None

    remaining = spec.rules.max_minutes_per_day - minutes_used(day, held)
    if remaining < spec.want.min_minutes:
        return None

    all_runs = runs(slots)
    wanted = min(spec.want.minutes, remaining)
    step = spec.want.step_minutes

    while wanted >= spec.want.min_minutes:
        blocks = [
            b
            for b in candidates(all_runs, spec, window_start, window_end, wanted)
            if not excluded(b, spec, labels) and not overlaps(b, held)
        ]
        if blocks:
            return min(blocks, key=lambda b: _rank(b, spec, labels))
        wanted -= step
    return None


def window_edge(today: date, window_days: int) -> date:
    return today + timedelta(days=window_days)
