"""Filling in what only the confirmation mail knows.

LibCal returns nothing useful when a booking is made: the check-in code and the
cancel token arrive by mail instead. Read them out of the mailbox and the ledger
becomes complete enough to check in and to cancel.

The scraping itself happens in the browser, since the mailbox needs a login.
This module only takes the rows it produced and matches them to what is held.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from uctbook.catalogue import Catalogue, Seat
from uctbook.models import HeldBooking
from uctbook.state import Ledger


def parse_when(when: str, clock: str, tz: ZoneInfo) -> datetime:
    """ "Monday 28 September 2026" plus "8:00am" as the library sees it."""
    stamp = datetime.strptime(f"{when} {clock.upper()}", "%A %d %B %Y %I:%M%p")
    return stamp.replace(tzinfo=tz)


def apply(ledger: Ledger, rows: list[dict[str, str]], tz: ZoneInfo, labels: dict[int, str]) -> int:
    """Match each mail to its booking on start time and resource name."""
    filled = 0
    for row in rows:
        try:
            start = parse_when(row["when"], row["from"], tz)
        except ValueError:
            continue
        wanted = row["what"].strip().casefold()
        for booking in ledger.bookings:
            if booking.start != start:
                continue
            name = labels.get(booking.key, "").strip().casefold()
            if name and name != wanted:
                continue
            if row.get("code"):
                booking.checkin_code = row["code"]
            if row.get("token"):
                booking.cancel_token = row["token"]
            filled += 1
            break
    if filled:
        ledger.save()
    return filled


def adopt(
    ledger: Ledger,
    rows: list[dict[str, str]],
    tz: ZoneInfo,
    catalogue: Catalogue,
) -> int:
    """Create ledger entries for mails with no booking behind them.

    The mailbox is the real record of what is held: anything booked before the
    ledger existed, or from another machine, only shows up here.
    """
    by_name = {
        seat.name.strip().casefold(): seat for seat in catalogue.seats if seat.name
    }
    added = 0
    for row in rows:
        try:
            start = parse_when(row["when"], row["from"], tz)
            end = parse_when(row["when"], row["to"], tz)
        except ValueError:
            continue
        if end <= start:
            continue
        seat = by_name.get(row["what"].strip().casefold())
        if seat is None:
            continue
        if any(b.start == start and b.key == seat.id for b in ledger.bookings):
            continue

        booking = HeldBooking(
            ref=f"mail|{start.date()}|{start:%H:%M}|{seat.id}",
            target=_target_for(catalogue, seat),
            day=start.date(),
            start=start,
            end=end,
            key=seat.id,
            checkin_code=row.get("code", ""),
            cancel_token=row.get("token", ""),
        )
        ledger.bookings.append(booking)
        added += 1
    if added:
        ledger.save()
    return added


def _target_for(catalogue: Catalogue, seat: Seat) -> str:
    """Name the category the way config does, so quota sums still line up."""
    for loc in catalogue.locations:
        if loc.id != seat.location:
            continue
        for category in loc.categories:
            if category.id == seat.category:
                return category.name
    return "unknown"


def load_rows(path: Path) -> list[dict[str, str]]:
    return list(json.loads(path.read_text()))
