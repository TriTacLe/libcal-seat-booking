# What UCT LibCal actually enforces

Everything here was learned by being refused. Each quote is the server's own wording, seen on
21 September 2026. Nothing in this file is inferred from documentation, because there is none.

## Rules that apply everywhere

**Quota is per category, not per account.**

> Sorry, this exceeds the 240 minute per day limit in this category.

240 minutes a day in each of the seven categories. Proven by booking Business Corner on a day
already capped for KC Computers, then a group study room on the same day again.

**Overlap between categories is allowed.** Three bookings covering the same hour in three different
categories were all accepted. Overlap *within* one category is always refused.

**Refusals come back as HTTP 500**, or 400 from the hold call, with the reason in the body. A 200
is the only thing that means success; status alone never tells you a booking failed.

**Booking needs no login.** The gate is a `@uct.ac.za` or `@myuct.ac.za` email address. Availability,
hold and submit all work signed out.

**Check-in**: opens 5 minutes before the start, and the booking is released if you have not checked
in within 15 minutes, or if the seat sits empty for 15 minutes.

## Per category

| Category | Window | Hours | Slot | Per day | Per booking | Gap between | Count |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Knowledge Commons Computers | 14 days | 08:00-19:30 | 5 min | 240 min | 240 min | none | unlimited |
| KC Group Study Rooms | 9 days | 08:00-22:00 | 5 min | 240 min | 180 min | 3 hours | unlimited |
| Seminar Room | 9 days | 08:00-22:00 | 5 min | 240 min | untested | untested | unlimited |
| Business Corner | 9 days | 08:00-22:00 | 5 min | 240 min | 240 min | none | unlimited |
| Snape Lounge Rooms | 9 days | 08:00-22:00 | 5 min | 240 min | 180 min | 3 hours | unlimited |
| Chris Hani Training | 9 days | 08:00-22:00 | 5 min | 240 min | 180 min | 3 hours | unlimited |
| LSEG Workspace | 9 days | 24 hours | **60 min** | 240 min | 120 min | none | **1 per day** |

The quotes behind the less obvious ones:

> This category has a 180 minute limit per booking. Please reduce the length of your 240 minute
> booking.

> This booking is too close to a previous booking you have made. This category has a limit of
> 3 hours between bookings.

> Sorry, this exceeds the 1 booking per day limit in this category.

## Custom questions differ per category

Every category asks its own set, and a missing one gives `Required fields were not supplied`.
`uct fields --target X` reads the live form and says which are unanswered.

- Everywhere: `fname`, `lname`, `email`, `q10382` student number, `q18840` faculty.
- KC Computers, Business Corner: `q18627` affiliation, `q18663[]`, `q18665[]`.
- Group and lounge rooms: also `q19807` group members' numbers and `q18664[]`.
- Seminar Room: `q22603` affiliation and `q22604[]`, different ids for the same questions.
- LSEG: `q18628[]`, which is **two** checkboxes sharing one name, both required:
  `Terms and Conditions ` (note the trailing space) and `Check out in 15 min`.

The form also carries honeypot fields, named things like `Address`, `DOB`, `Name` and `Feedback`,
inside a `slc-hi` div that a script removes. Real questions are always `qNNNNN`. Filling a honeypot
is how a bot announces itself, so they are skipped.

## Closures seen

Sundays. 24 September, Heritage Day. KC Computers returned nothing on Saturdays, while the group
rooms did have Saturday availability.

## Rate limits

Not probed, and deliberately so. Nothing observed has looked like throttling, but finding the
ceiling means hammering a university server, and the booking form links an admin banned-users list
at `/admin/equipment/banned`. The tool spaces its calls with `--pause`, default 1.5 seconds, and
makes one availability read per booking because a booking invalidates the checksums it used.
