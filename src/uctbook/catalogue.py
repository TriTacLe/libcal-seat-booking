"""The lid/zone/gid ids LibCal uses are meaningless to read, and UCT can rename
things, so they get scraped once into data/catalogue.yaml and the config refers
to everything by name.

Zones, categories and capacities differ per location, so they are nested under
one. /r/new only ships the location select until a location is chosen, hence one
fetch per location.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import httpx
import yaml
from pydantic import BaseModel

HOST = "uct.ac.za.libcal.com"
NEW_RESERVATION_URL = f"https://{HOST}/r/new"
SPACES_URL = f"https://{HOST}/spaces"

_SELECT_RE = re.compile(r'<select[^>]*name="(\w+)"[^>]*>(.*?)</select>', re.S)
_OPTION_RE = re.compile(r'<option[^>]*value="(-?\d+)"[^>]*>(.*?)</option>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# The /spaces page declares every resource in a resources.push({...}) block.
# Seat bookings quote with ', room bookings with ", and only seats carry seatId.
_RESOURCE_RE = re.compile(r"resources\.push\(\{(.*?)\}\);", re.S)
_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _field(block: str, name: str) -> str | None:
    quoted = re.search(rf"{name}:\s*['\"]((?:\\.|[^'\"])*)['\"]", block)
    if quoted:
        return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), quoted.group(1))
    number = re.search(rf"{name}:\s*(-?\d+)", block)
    return number.group(1) if number else None


class Entry(BaseModel):
    id: int
    name: str


class Seat(BaseModel):
    id: int
    name: str = ""
    eid: int = 0
    location: int = 0
    category: int = 0
    capacity: int = 0


class Location(BaseModel):
    id: int
    name: str
    zones: list[Entry] = []
    categories: list[Entry] = []
    capacities: list[Entry] = []


class Catalogue(BaseModel):
    version: int = 1
    host: str = HOST
    locations: list[Location] = []
    seats: list[Seat] = []

    def location(self, name: str) -> Location:
        wanted = name.strip().casefold()
        for loc in self.locations:
            if loc.name.casefold() == wanted:
                return loc
        known = ", ".join(loc.name for loc in self.locations) or "nothing"
        raise KeyError(f"No location named '{name}'. Catalogue has: {known}")

    def _in(self, entries: list[Entry], name: str, what: str, where: str) -> int:
        wanted = name.strip().casefold()
        for entry in entries:
            if entry.name.casefold() == wanted:
                return entry.id
        known = ", ".join(e.name for e in entries) or "nothing"
        raise KeyError(f"No {what} named '{name}' at {where}. Catalogue has: {known}")

    def zone(self, location: str, name: str) -> int:
        loc = self.location(location)
        return self._in(loc.zones, name, "zone", loc.name)

    def category(self, location: str, name: str) -> int:
        loc = self.location(location)
        return self._in(loc.categories, name, "category", loc.name)

    def capacity(self, location: str, name: str) -> int:
        loc = self.location(location)
        return self._in(loc.capacities, name, "capacity", loc.name)

    def label_for(self, key: int) -> str:
        for seat in self.seats:
            if seat.id == key:
                return seat.name
        return ""

    def eid_by_seat(self) -> dict[int, int]:
        return {seat.id: seat.eid for seat in self.seats if seat.eid}

    def labels(self) -> dict[int, str]:
        return {seat.id: seat.name for seat in self.seats if seat.name}


def _clean(text: str) -> str:
    return " ".join(_TAG_RE.sub("", text).split())


def _options(html: str, select_name: str) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[int] = set()
    for name, block in _SELECT_RE.findall(html):
        if name != select_name:
            continue
        for value, label in _OPTION_RE.findall(block):
            ident = int(value)
            text = _clean(label)
            if not text or text == "Show All" or text.lower().startswith("select a"):
                continue
            if ident in seen:
                continue
            seen.add(ident)
            entries.append(Entry(id=ident, name=text))
    return entries


def parse_locations(html: str) -> list[Entry]:
    return _options(html, "lid")


def parse_location_detail(html: str) -> tuple[list[Entry], list[Entry], list[Entry]]:
    return _options(html, "zone"), _options(html, "gid"), _options(html, "capacity")


def parse_resources(html: str, lid: int, gid: int) -> list[Seat]:
    seats: list[Seat] = []
    for block in _RESOURCE_RE.findall(html):
        eid = _field(block, "eid")
        if eid is None:
            continue
        seat_id = _field(block, "seatId")
        capacity = _field(block, "capacity")
        seats.append(
            Seat(
                id=int(seat_id) if seat_id else int(eid),
                name=_field(block, "title") or "",
                eid=int(eid),
                location=lid,
                category=gid,
                capacity=int(capacity) if capacity else 0,
            )
        )
    return seats


def fetch_resources(client: httpx.Client, lid: int, gid: int) -> list[Seat]:
    resp = client.get(SPACES_URL, params={"lid": lid, "gid": gid})
    resp.raise_for_status()
    return parse_resources(resp.text, lid, gid)


def fetch_catalogue(client: httpx.Client) -> Catalogue:
    root = client.get(NEW_RESERVATION_URL)
    root.raise_for_status()

    catalogue = Catalogue()
    for entry in parse_locations(root.text):
        detail = client.get(NEW_RESERVATION_URL, params={"lid": entry.id})
        detail.raise_for_status()
        zones, categories, capacities = parse_location_detail(detail.text)
        catalogue.locations.append(
            Location(
                id=entry.id,
                name=entry.name,
                zones=zones,
                categories=categories,
                capacities=capacities,
            )
        )
    return catalogue


def merge_seats(catalogue: Catalogue, found: Iterable[Seat]) -> None:
    """Keep hand-written names; only fill in what we did not have."""
    by_id = {seat.id: seat for seat in catalogue.seats}
    for seat in found:
        existing = by_id.get(seat.id)
        if existing is None:
            by_id[seat.id] = seat
            continue
        if not existing.name and seat.name:
            existing.name = seat.name
        existing.eid = existing.eid or seat.eid
        existing.location = existing.location or seat.location
        existing.category = existing.category or seat.category
        existing.capacity = existing.capacity or seat.capacity
    catalogue.seats = sorted(by_id.values(), key=lambda s: s.id)


def load(path: Path) -> Catalogue:
    if not path.exists():
        raise FileNotFoundError(f"No catalogue at {path}. Run 'uct discover' first.")
    return Catalogue.model_validate(yaml.safe_load(path.read_text()))


def save(catalogue: Catalogue, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(catalogue.model_dump(), sort_keys=False, allow_unicode=True))
