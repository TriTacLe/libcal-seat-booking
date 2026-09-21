from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from uctbook import catalogue as cat
from uctbook.clock import today as today_in
from uctbook.config import Config, TargetSpec, load_config
from uctbook.libcal import GridQuery, LibCalError, ReadClient
from uctbook.planner import choose, window_edge

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
load_dotenv(Path(".env"), override=False)


def _logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _load(config: Path) -> Config:
    try:
        return load_config(config)
    except FileNotFoundError:
        print(f"No config at {config}", file=sys.stderr)
        raise typer.Exit(1)
    except ValueError as e:
        print(f"Bad config: {e}", file=sys.stderr)
        raise typer.Exit(1)


def _query(spec: TargetSpec, catalogue: cat.Catalogue) -> GridQuery:
    return GridQuery(
        lid=catalogue.location(spec.location).id,
        gid=catalogue.category(spec.location, spec.category) if spec.category else 0,
        zone=catalogue.zone(spec.location, spec.zone) if spec.zone else 0,
        capacity=catalogue.capacity(spec.location, spec.capacity) if spec.capacity else -1,
        filters=spec.filters,
    )


@app.command()
def discover(
    out: Path = typer.Option(Path("data/catalogue.yaml"), "--out", "-o"),
    probe: bool = typer.Option(True, help="Also map seats to resources via the accessible page."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scrape the locations, zones and categories LibCal offers into a catalogue."""
    _logging(verbose)
    with httpx.Client(timeout=60.0, follow_redirects=True) as http:
        catalogue = cat.fetch_catalogue(http)
        for loc in catalogue.locations:
            print(f"{loc.id:>6}  {loc.name}")
            for zone in loc.zones:
                print(f"          zone     {zone.id:>6}  {zone.name}")
            for category in loc.categories:
                print(f"          category {category.id:>6}  {category.name}")
            for capacity in loc.capacities:
                print(f"          capacity {capacity.id:>6}  {capacity.name}")

        if probe:
            found: list[cat.Seat] = []
            for loc in catalogue.locations:
                for category in loc.categories:
                    resources = cat.fetch_resources(http, loc.id, category.id)
                    if not resources:
                        continue
                    logging.info(
                        "lid=%s gid=%s: %d resource(s)", loc.id, category.id, len(resources)
                    )
                    found += resources
            cat.merge_seats(catalogue, found)
            print(f"Resources: {len(catalogue.seats)}")

    cat.save(catalogue, out)
    print(f"Wrote {out}")


@app.command()
def slots(
    target: str = typer.Option(..., "--target", "-t"),
    day: str = typer.Option("", "--date", "-d", help="YYYY-MM-DD, default the window edge."),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Print availability for one target and day."""
    _logging(verbose)
    cfg = _load(config)
    spec = cfg.target(target)
    catalogue = cat.load(cfg.catalogue)
    tz = cfg.zone
    wanted = date.fromisoformat(day) if day else window_edge(today_in(tz), spec.rules.window_days)

    client = ReadClient(tz, eid_by_seat=catalogue.eid_by_seat())
    try:
        page = client.grid(_query(spec, catalogue), wanted)
    except LibCalError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(1)
    finally:
        client.close()

    if page.window_end:
        print(f"{wanted} is past the booking window ({spec.rules.window_days} days).")
        raise typer.Exit(0)

    free = [s for s in page.slots if s.free]
    keys = sorted({s.key for s in page.slots})
    print(f"{wanted}: {len(page.slots)} slots over {len(keys)} resource(s), {len(free)} free")
    for key in keys:
        label = catalogue.label_for(key) or str(key)
        open_slots = [s for s in free if s.key == key]
        if not open_slots:
            print(f"  {label:<8} full")
            continue
        first, last = open_slots[0].start, open_slots[-1].end
        mins = len(open_slots) * spec.rules.slot_minutes
        print(
            f"  {label:<8} {mins:>4} min free, "
            f"{first.strftime('%H:%M')}-{last.strftime('%H:%M')}"
        )


@app.command()
def plan(
    target: str = typer.Option("", "--target", "-t", help="Default: every enabled target."),
    day: str = typer.Option("", "--date", "-d"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show the block the booker would take, without booking anything."""
    _logging(verbose)
    cfg = _load(config)
    catalogue = cat.load(cfg.catalogue)
    tz = cfg.zone
    specs = [cfg.target(target)] if target else cfg.active()

    client = ReadClient(tz, eid_by_seat=catalogue.eid_by_seat())
    try:
        for spec in specs:
            wanted = (
                date.fromisoformat(day)
                if day
                else window_edge(today_in(tz), spec.rules.window_days)
            )
            page = client.grid(_query(spec, catalogue), wanted)
            if page.window_end:
                print(f"{spec.name}: {wanted} is past the booking window")
                continue
            labels = catalogue.labels()
            block = choose(page.slots, spec, wanted, [], labels)
            if block is None:
                print(f"{spec.name}: nothing bookable on {wanted}")
                continue
            label = labels.get(block.key, str(block.key))
            print(
                f"{spec.name}: {wanted} {block.start.strftime('%H:%M')}-"
                f"{block.end.strftime('%H:%M')} on {label} ({block.minutes} min, "
                f"{len(block.slots)} slots)"
            )
    except LibCalError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    app()
