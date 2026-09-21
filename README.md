# uct-room-booking

Automates seat and room booking on UCT's LibCal at `uct.ac.za.libcal.com`.

Reading availability needs no login, so `discover`, `slots` and `plan` work out of the box.
Booking needs the UCT SSO session and is not wired up yet.

## Install

```bash
uv sync
```

## Commands

```bash
uv run uct discover                      # write data/catalogue.yaml from the live site
uv run uct slots -t kc-computers         # availability for the window edge
uv run uct slots -t kc-computers -d 2026-10-05
uv run uct plan                          # the block it would book, for every enabled target
```

`discover` writes the catalogue that `config.yaml` refers to by name. Re-run it when UCT
renames or adds something; the file is committed so the change shows up as a diff.

## Config

`config.yaml` holds one entry per target. Rules live per target because the three locations
publish different limits.

- `rules` is what the library allows: slot length, how far ahead the window opens, the daily
  cap, opening hours.
- `want` is what you are after: weekdays, the window within the day, the block length, and the
  shortest block worth taking instead of nothing.
- `items.prefer` and `items.exclude` take either a printed name (`KC19`) or a numeric id.

## What LibCal does

- Availability comes from `POST /spaces/availability/grid` in 5-minute slots. A slot with no
  `className` is bookable; any `className` marks a state that is not.
- `itemId` in that response is the seat. Whole rooms report seat 0 and are identified by their
  resource id instead, which is why the catalogue keys on `seat_id or eid`.
- The window is exactly 14 days. Past it the response carries `windowEnd: true` and no slots,
  so the edge never has to be guessed.
- Every slot carries its own checksum, and a booking is submitted as the ordered list of the
  slots it covers, each with its checksum. A 4-hour booking is 48 of them.
- Published limits: 4 hours per day, non-consecutive allowed; check-in opens 5 minutes before
  the start and the booking is cancelled if you have not checked in within 15 minutes; no
  booking is needed between 19:30 and 08:00.

## Times

LibCal sends naive timestamps that mean wall-clock time in Africa/Johannesburg, and it does no
conversion. South Africa has no DST, so the library side never shifts, but a machine in Europe
does. Every wall-clock read goes through `clock.py`, and a test fails the build if that stops
being true. Timers are scheduled in UTC for the same reason.
