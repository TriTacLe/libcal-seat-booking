from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from uctbook import auth
from uctbook import catalogue as cat
from uctbook.clock import today as today_in
from uctbook.config import Config, TargetSpec, load_config
from uctbook.libcal import GridQuery, LibCalError, ReadClient
from uctbook.planner import choose, window_edge
from uctbook.recon import Capture, account_secrets

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
recon_app = typer.Typer(add_completion=False, help="Record the booking calls once, by hand.")
app.add_typer(recon_app, name="recon")
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
    lid = catalogue.location(spec.location).id
    gid = catalogue.category(spec.location, spec.category) if spec.category else 0
    return GridQuery(
        lid=lid,
        gid=gid,
        zone=catalogue.zone(spec.location, spec.zone) if spec.zone else 0,
        capacity=catalogue.capacity(spec.location, spec.capacity) if spec.capacity else -1,
        filters=spec.filters,
        seat_booking=catalogue.is_seat_booking(lid, gid),
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


@app.command()
def login(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Open a browser, sign in to UCT, and keep the session.

    Records which hosts the sign-in passes through and which cookies each one
    sets, so the automatic re-auth can be written against what actually happens
    rather than a guess. Cookie values are never written down.
    """
    _logging(verbose)
    with sync_playwright() as p:
        browser = auth.launch(p, headless=False)
        ctx = auth.new_context(browser)
        page = ctx.new_page()
        recorder = auth.ChainRecorder(page, ctx)
        auth.goto_with_retry(page, auth.ENTRY_URL)

        print("Sign in to UCT in the browser window, then come back here.")
        print("Stop once LibCal shows you as signed in.")
        input("Press Enter when you are done: ")

        auth.save_session(ctx)
        out = recorder.write()
        signed_in = auth.is_authenticated(page)
        auth.close_session(browser, ctx, page)

    print(f"Session saved to {auth.STATE_FILE}")
    print(f"Sign-in chain recorded in {out}")
    if not signed_in:
        print(
            "Could not see a signed-in marker on the page. The session was still "
            "saved; check the recorded page.html before relying on it.",
            file=sys.stderr,
        )


@recon_app.command("capture")
def recon_capture(
    label: str = typer.Option("booking", "--label", "-l"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Record one real booking made by hand.

    Make a short booking you actually want in the browser window. Every LibCal
    call is written out with its request and response so the submit payload can
    be implemented against evidence. Cancel it afterwards with the link in the
    confirmation mail if you no longer want it.
    """
    _logging(verbose)
    with sync_playwright() as p:
        browser = auth.launch(p, headless=False)
        ctx = auth.new_context(browser)
        page = ctx.new_page()
        capture = Capture(page, account_secrets())
        auth.goto_with_retry(page, auth.ENTRY_URL)

        print("Make one short booking in the browser window.")
        print("Go all the way to the confirmation page.")
        input("Press Enter once the booking is confirmed: ")

        auth.save_session(ctx)
        out = capture.write(label)
        auth.close_session(browser, ctx, page)

    print(f"Captured {len(capture.calls)} call(s) into {out}")
    print(f"Read {out / 'summary.md'} for the call sequence.")


if __name__ == "__main__":
    app()
