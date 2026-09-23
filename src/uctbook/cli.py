from __future__ import annotations

import logging
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import typer
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from uctbook import accessible, auth, mailimport, notifier
from uctbook import catalogue as cat
from uctbook.clock import now as now_in
from uctbook.clock import today as today_in
from uctbook.config import Config, TargetSpec, load_config
from uctbook.libcal import (
    BookingClient,
    CategoryFull,
    GridQuery,
    LibCalError,
    ReadClient,
    check_in,
    release,
)
from uctbook.libcal import (
    cancel as libcal_cancel,
)
from uctbook.models import HeldBooking
from uctbook.planner import choose, window_edge
from uctbook.recon import Capture, account_secrets
from uctbook.state import Ledger

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
    wanted = _as_date(day, tz) if day else window_edge(today_in(tz), spec.rules.window_days)

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
                _as_date(day, tz)
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
def status(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    all_rows: bool = typer.Option(False, "--all", help="Include past bookings."),
) -> None:
    """List what the ledger says is booked."""
    cfg = _load(config)
    tz = cfg.zone
    ledger = Ledger.load(cfg.state, tz)
    rows = ledger.bookings if all_rows else ledger.live(now_in(tz))
    if not rows:
        print("Nothing booked." if all_rows else "Nothing upcoming.")
        return

    by_day: dict[date, list[HeldBooking]] = {}
    for booking in sorted(rows, key=lambda b: (b.day, b.start)):
        by_day.setdefault(booking.day, []).append(booking)

    total = 0
    for wanted, bookings in by_day.items():
        minutes = sum(int((b.end - b.start).total_seconds() // 60) for b in bookings)
        total += minutes
        print(f"\n{wanted:%a %d %b}  {len(bookings)} booking(s), {minutes} min")
        for b in bookings:
            code = b.checkin_code or "no code"
            print(
                f"  {b.start:%H:%M}-{b.end:%H:%M}  {b.target:<24} {code:<8} "
                f"{'cancellable' if b.cancel_token else ''}"
            )
    print(f"\n{len(rows)} booking(s), {total} min total.")


@app.command()
def mail_import(
    rows: Path = typer.Argument(..., help="JSON produced by scraping the mailbox."),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
) -> None:
    """Fill in check-in codes and cancel tokens from the confirmation mails."""
    cfg = _load(config)
    tz = cfg.zone
    ledger = Ledger.load(cfg.state, tz)
    catalogue = cat.load(cfg.catalogue)
    labels = {seat.id: seat.name for seat in catalogue.seats}

    parsed = mailimport.load_rows(rows)
    filled = mailimport.apply(ledger, parsed, tz, labels)
    added = mailimport.adopt(ledger, parsed, tz, catalogue)
    missing = [b for b in ledger.live(now_in(tz)) if not b.checkin_code]
    print(f"Filled {filled}, adopted {added}. {len(missing)} upcoming still without a code.")


@app.command()
def digest(
    send: bool = typer.Option(False, "--send", help="Mail it as well as printing it."),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
) -> None:
    """One overview of everything booked, instead of a mail per booking."""
    cfg = _load(config)
    tz = cfg.zone
    ledger = Ledger.load(cfg.state, tz)
    catalogue = cat.load(cfg.catalogue)
    places = _places(catalogue)
    upcoming = ledger.live(now_in(tz))

    print(notifier.schedule(upcoming, places))
    if send and not notifier.digest(cfg, upcoming, places):
        print("Mail not sent: check the gmail settings in the config.", file=sys.stderr)


def _places(catalogue: cat.Catalogue) -> dict[int, str]:
    """Resource id to something worth reading in a mail."""
    where = {loc.id: loc.name for loc in catalogue.locations}
    out = {}
    for seat in catalogue.seats:
        building = where.get(seat.location, "")
        out[seat.id] = f"{seat.name or seat.id}, {building}" if building else seat.name
    return out


@app.command()
def cancel(
    day: str = typer.Option("", "--date", "-d", help="Only this day."),
    target: str = typer.Option("", "--target", "-t", help="Only this category."),
    commit: bool = typer.Option(False, "--commit", help="Actually cancel."),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
) -> None:
    """Release bookings. Dry run unless --commit is given."""
    cfg = _load(config)
    tz = cfg.zone
    ledger = Ledger.load(cfg.state, tz)

    wanted = [b for b in ledger.live(now_in(tz))]
    if day:
        chosen = _as_date(day, tz)
        wanted = [b for b in wanted if b.day == chosen]
    if target:
        wanted = [b for b in wanted if b.target == target]
    if not wanted:
        print("Nothing matches.")
        return

    booker = BookingClient(tz)
    released = 0
    try:
        for booking in wanted:
            when = f"{booking.day} {booking.start:%H:%M}-{booking.end:%H:%M}"
            if not booking.cancel_token:
                print(f"{booking.target}: {when} has no cancel token, use the mail link")
                continue
            if not commit:
                print(f"{booking.target}: would cancel {when}")
                continue
            if libcal_cancel(booking.cancel_token, booker.http):
                booking.status = "cancelled"
                released += 1
                print(f"{booking.target}: cancelled {when}")
            else:
                print(f"{booking.target}: cancel refused for {when}", file=sys.stderr)
    finally:
        booker.close()
        if commit:
            ledger.save()
    if commit:
        print(f"\nCancelled {released}.")


@app.command()
def checkin(
    commit: bool = typer.Option(False, "--commit", help="Actually check in."),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
) -> None:
    """Check in to any booking that has just started.

    A booking is released if nobody checks in within 15 minutes. That rule is
    how the library recovers seats from no-shows, so this only makes sense for
    bookings you are actually going to use.
    """
    cfg = _load(config)
    tz = cfg.zone
    if not cfg.checkin.enabled:
        print("checkin.enabled is false in the config.")
        return

    ledger = Ledger.load(cfg.state, tz)
    due = ledger.due_checkin(now_in(tz), cfg.checkin.offset_minutes)
    if not due:
        print("Nothing due.")
        return

    booker = BookingClient(tz)
    done = 0
    try:
        for booking in due:
            when = f"{booking.day} {booking.start:%H:%M}"
            if not commit:
                print(f"{booking.target}: would check in {when} with {booking.checkin_code}")
                continue
            if check_in(booking.checkin_code, booker.http):
                booking.status = "checked-in"
                done += 1
                print(f"{booking.target}: checked in {when}")
            else:
                print(f"{booking.target}: check-in failed {when}", file=sys.stderr)
    finally:
        booker.close()
        if commit:
            ledger.save()
    if commit:
        print(f"\nChecked in {done}.")


@app.command()
def fields(
    target: str = typer.Option(..., "--target", "-t"),
    day: str = typer.Option("", "--date", "-d"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show the questions a category's booking form asks, then let the hold go.

    Categories do not ask the same things, and UCT can add a question whenever
    they like, so this reads the form rather than trusting the config.
    """
    _logging(verbose)
    cfg = _load(config)
    catalogue = cat.load(cfg.catalogue)
    tz = cfg.zone
    spec = cfg.target(target)
    wanted = _as_date(day, tz) if day else window_edge(today_in(tz), spec.rules.window_days)

    query = _query(spec, catalogue)
    booker = BookingClient(tz)
    try:
        html = accessible.fetch(
            query.lid, query.gid, query.zone, query.capacity, wanted, booker.http
        )
        slots = accessible.parse_slots(html, tz)
        block = choose(slots, spec, wanted, [], accessible.parse_labels(html))
        if block is None:
            print(f"{spec.name}: nothing free on {wanted} to hold.", file=sys.stderr)
            raise typer.Exit(1)

        hold = booker.hold(block, query.lid, query.gid, query.zone, query.capacity)
        try:
            asked = _form_fields(hold.html)
            answers = {**cfg.patron.fields(), **spec.answers}
            print(f"{spec.name}, form on {wanted}:")
            for name, label in asked:
                state = "answered" if name in answers else "MISSING"
                print(f"  {name:<12} {state:<9} {label}")
            missing = [n for n, _ in asked if n not in answers]
            if missing:
                print(f"\nAdd to the target's answers: {', '.join(missing)}")
        finally:
            release(hold, booker.http)
            print("\nHold released.")
    except LibCalError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(1)
    finally:
        booker.close()


def _form_fields(html: str) -> list[tuple[str, str]]:
    """Required inputs on the booking form, with their labels.

    Real custom questions are always qNNNNN. Anything else alongside the name
    and email fields is a trap LibCal plants and strips with a script, and
    filling one is how a bot announces itself.
    """
    known = {"fname", "lname", "email"}
    labels = dict(re.findall(r'for="(q\d+)"[^>]*>\s*([^<\n]{2,60}?)\s*\n', html))
    seen: list[tuple[str, str]] = []
    for name in re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"', html):
        bare = name.removesuffix("[]")
        if bare not in known and not re.fullmatch(r"q\d+", bare):
            continue
        if any(name == s[0] for s in seen):
            continue
        seen.append((name, labels.get(bare, "")))
    return seen


@app.command()
def book(
    target: str = typer.Option("", "--target", "-t", help="Default: every enabled target."),
    day: str = typer.Option("", "--date", "-d"),
    from_date: str = typer.Option("", "--from", help="First day of a range."),
    to_date: str = typer.Option("", "--to", help="Last day of a range, window edge if omitted."),
    start_time: str = typer.Option("", "--start-time", help="Override the daily search window."),
    end_time: str = typer.Option("", "--end-time"),
    pause: float = typer.Option(1.5, "--pause", help="Seconds between LibCal calls."),
    commit: bool = typer.Option(False, "--commit", help="Actually book. Without it, a dry run."),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Book the planned block. Dry run unless --commit is given."""
    _logging(verbose)
    cfg = _load(config)
    catalogue = cat.load(cfg.catalogue)
    tz = cfg.zone
    specs = [cfg.target(target)] if target else cfg.active()

    if commit and not cfg.patron.email:
        print("No patron email in the config, so there is nothing to book with.", file=sys.stderr)
        raise typer.Exit(1)

    ledger = Ledger.load(cfg.state, tz)
    before = {b.ref for b in ledger.bookings}
    reader = ReadClient(tz, eid_by_seat=catalogue.eid_by_seat())
    booker = BookingClient(tz)
    booked = failed = 0
    try:
        for spec in specs:
            if start_time:
                spec.want.earliest_start = start_time
            if end_time:
                spec.want.latest_end = end_time
            for wanted in _days(spec, tz, day, from_date, to_date):
                made, lost = _fill_category(
                    spec, wanted, catalogue, reader, booker, cfg, ledger, commit, tz, pause
                )
                booked += made
                failed += lost
    except LibCalError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(1)
    finally:
        reader.close()
        booker.close()
        if commit:
            ledger.save()

    if commit:
        print(f"\nBooked {booked}, failed {failed}.")
        fresh = [b for b in ledger.bookings if b.ref not in before]
        if fresh:
            notifier.digest(cfg, ledger.live(now_in(tz)), _places(catalogue))
    if failed:
        raise typer.Exit(1)


def _as_date(value: str, tz: ZoneInfo) -> date:
    """Accepts a date, or "today", which is what a timer wants to say."""
    if value.strip().casefold() in ("today", "now"):
        return today_in(tz)
    return date.fromisoformat(value)


def _days(spec: TargetSpec, tz: ZoneInfo, day: str, from_date: str, to_date: str) -> list[date]:
    edge = window_edge(today_in(tz), spec.rules.window_days)
    if day:
        return [_as_date(day, tz)]
    if not from_date:
        return [edge]
    cursor = _as_date(from_date, tz)
    last = _as_date(to_date, tz) if to_date else edge
    days = []
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _fill_category(
    spec: TargetSpec,
    wanted: date,
    catalogue: cat.Catalogue,
    reader: ReadClient,
    booker: BookingClient,
    cfg: Config,
    ledger: Ledger,
    commit: bool,
    tz: ZoneInfo,
    pause: float,
) -> tuple[int, int]:
    """Book this category until its own daily cap is used up.

    Prefers blocks that do not clash with anything already held, and only falls
    back to an overlapping one when the free time has run out, because securing
    the hours matters more than them being usable back to back.
    """
    booked = failed = 0
    query = _query(spec, catalogue)
    page = reader.grid(query, wanted)
    if page.window_end:
        print(f"{spec.name}: {wanted} is past the booking window")
        return 0, 0

    if ledger.is_full(spec.name, wanted):
        print(f"{spec.name}: {wanted} already known full")
        return 0, 0

    while True:
        used = ledger.minutes(wanted, spec.name)
        if spec.rules.max_minutes_per_day - used < spec.want.min_minutes:
            break
        ceiling = cfg.max_bookings_per_day
        if ceiling is not None and ledger.count(wanted) >= ceiling:
            break
        own = spec.rules.max_bookings_per_day
        if own is not None and len(
            [b for b in ledger.for_day(wanted) if b.target == spec.name]
        ) >= own:
            break

        # Re-read every time: booking a block invalidates the checksums it used.
        html = accessible.fetch(
            query.lid, query.gid, query.zone, query.capacity, wanted, booker.http
        )
        slots = accessible.parse_slots(html, tz)
        labels = {**catalogue.labels(), **accessible.parse_labels(html)}
        held = ledger.for_day(wanted)

        block = choose(slots, spec, wanted, held, labels)
        if block is None and cfg.allow_overlap:
            block = choose(slots, spec, wanted, held, labels, allow_overlap=True)
        if block is None:
            if booked == 0:
                print(f"{spec.name}: nothing bookable on {wanted}")
            break
        if ledger.has(spec.name, block):
            break

        label = labels.get(block.key, str(block.key))
        when = f"{wanted} {block.start.strftime('%H:%M')}-{block.end.strftime('%H:%M')}"
        if not commit:
            print(f"{spec.name}: would book {label} {when} ({block.minutes} min)")
            ledger.add(spec.name, block)
            continue

        try:
            hold = booker.hold(block, query.lid, query.gid, query.zone, query.capacity)
        except CategoryFull as e:
            ledger.mark_full(spec.name, wanted, str(e))
            print(f"{spec.name}: {wanted} is full ({e})")
            break
        except LibCalError as e:
            print(f"{spec.name}: could not hold {label} {when}: {e}", file=sys.stderr)
            failed += 1
            break

        try:
            done = booker.submit(
                hold, block, query.lid, {**cfg.patron.fields(), **spec.answers}
            )
        except CategoryFull as e:
            release(hold, booker.http)
            ledger.mark_full(spec.name, wanted, str(e))
            print(f"{spec.name}: {wanted} is full ({e})")
            break
        except LibCalError as e:
            release(hold, booker.http)
            print(f"{spec.name}: refused {label} {when}: {e}", file=sys.stderr)
            failed += 1
            break

        entry = ledger.add(spec.name, block)
        entry.booking_id = done.booking_id
        entry.cancel_token = done.cancel_token
        entry.checkin_code = done.checkin_code
        ledger.save()
        booked += 1
        print(f"{spec.name}: booked {label} {when} ({block.minutes} min)")
        # Space the calls out. This is a university server, not a target.
        time.sleep(pause)

    return booked, failed


def _wait_for_confirmation(page: "object", capture: Capture, timeout_ms: int) -> None:
    """Stop once a booking has actually been submitted, so the capture is not
    cut short and does not sit open longer than it needs to."""
    waited = 0
    while waited < timeout_ms:
        try:
            page.wait_for_timeout(2_000)  # type: ignore[attr-defined]
        except Exception:
            return
        waited += 2_000
        if capture.saw_booking_submit():
            page.wait_for_timeout(3_000)  # type: ignore[attr-defined]
            return


@app.command()
def login(
    wait_minutes: int = typer.Option(10, "--wait-minutes", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Open a browser, sign in to UCT, and keep the session.

    Waits for a LibCal session cookie to appear rather than for a keypress.
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

        print("Sign in to UCT in the browser window that just opened.")
        print(f"Waiting up to {wait_minutes} minute(s) for the session.")
        signed_in = auth.wait_for_login(ctx, page, timeout_ms=wait_minutes * 60_000)

        auth.save_session(ctx)
        out = recorder.write()
        auth.close_session(browser, ctx, page)

    print(f"Sign-in chain recorded in {out}")
    if signed_in:
        print(f"Session saved to {auth.STATE_FILE}")
    else:
        print(
            f"No LibCal session cookie after {wait_minutes} minute(s). "
            f"Whatever was there is still saved; check {out}/page.png.",
            file=sys.stderr,
        )
        raise typer.Exit(1)


@recon_app.command("capture")
def recon_capture(
    label: str = typer.Option("booking", "--label", "-l"),
    wait_minutes: int = typer.Option(15, "--wait-minutes", "-w"),
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
        print(f"Recording for up to {wait_minutes} minute(s), or until the browser closes.")
        _wait_for_confirmation(page, capture, wait_minutes * 60_000)

        auth.save_session(ctx)
        out = capture.write(label)
        auth.close_session(browser, ctx, page)

    print(f"Captured {len(capture.calls)} call(s) into {out}")
    print(f"Read {out / 'summary.md'} for the call sequence.")


if __name__ == "__main__":
    app()
