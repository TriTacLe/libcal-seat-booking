"""Mail, over Gmail SMTP.

A run that books nothing sends nothing: a daily "no change" mail only teaches
you to ignore the sender.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from uctbook.config import Config
from uctbook.models import HeldBooking

logger = logging.getLogger(__name__)


def send(cfg: Config, subject: str, body: str, html: str = "") -> bool:
    """Never raises: a dead mail server must not cost a booking."""
    gmail = cfg.notifications.gmail
    if gmail is None:
        return False
    to, from_addr, password = gmail.resolve()
    recipients = gmail.all_recipients()
    if not (recipients and password):
        return False

    if html:
        message: Message = MIMEMultipart("alternative")
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(html, "html", "utf-8"))
    else:
        message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = from_addr or to
    message["To"] = ", ".join(recipients)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp:
            smtp.login(from_addr or to, password)
            smtp.sendmail(from_addr or to, recipients, message.as_string())
    except Exception as e:
        logger.info("could not send mail: %s", e)
        return False
    logger.info("mailed %s: %s", ", ".join(recipients), subject)
    return True


def schedule(bookings: list[HeldBooking], places: dict[int, str]) -> str:
    """One readable list of everything held, grouped by day.

    LibCal mails a separate confirmation per booking, which is unusable once
    there are dozens of them. This is the one that answers where to be and when.
    """
    if not bookings:
        return "Nothing booked."

    by_day: dict[date, list[HeldBooking]] = {}
    for b in sorted(bookings, key=lambda x: (x.day, x.start)):
        by_day.setdefault(b.day, []).append(b)

    lines = []
    for day, rows in by_day.items():
        minutes = sum(int((b.end - b.start).total_seconds() // 60) for b in rows)
        hours = minutes / 60
        lines.append(f"{day:%A %d %B}  ({hours:.1f}h)")
        for b in rows:
            where = places.get(b.key, str(b.key))
            code = f"  code {b.checkin_code}" if b.checkin_code else "  no code yet"
            lines.append(f"    {b.start:%H:%M}-{b.end:%H:%M}  {where}{code}")
        lines.append("")

    total = sum(int((b.end - b.start).total_seconds() // 60) for b in bookings)
    lines.append(f"{len(bookings)} bookings, {total / 60:.1f} hours in total.")
    lines.append("")
    lines.append(
        "Each one is released if nobody checks in within 15 minutes of the start, "
        "using the code in its own confirmation mail."
    )
    return "\n".join(lines)


def _rows_by_day(bookings: list[HeldBooking]) -> dict[date, list[HeldBooking]]:
    out: dict[date, list[HeldBooking]] = {}
    for b in sorted(bookings, key=lambda x: (x.day, x.start)):
        out.setdefault(b.day, []).append(b)
    return out


def _split_place(place: str) -> tuple[str, str]:
    room, _, building = place.partition(", ")
    return room, building


def as_html(bookings: list[HeldBooking], places: dict[int, str]) -> str:
    """A table per day. Plain colours, real font sizes, readable on a phone."""
    if not bookings:
        return "<p>Nothing booked.</p>"

    css_cell = "padding:8px 10px;border-bottom:1px solid #e3e3e3;font-size:15px;"
    parts = [
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'color:#1a1a1a;max-width:640px;">'
    ]
    for day, rows in _rows_by_day(bookings).items():
        minutes = sum(int((b.end - b.start).total_seconds() // 60) for b in rows)
        parts.append(
            f'<h2 style="font-size:17px;margin:22px 0 6px;">{day:%A %d %B}'
            f'<span style="font-weight:400;color:#595959;"> &middot; '
            f'{minutes / 60:.1f} hours</span></h2>'
        )
        parts.append('<table style="border-collapse:collapse;width:100%;">')
        for b in rows:
            room, building = _split_place(places.get(b.key, str(b.key)))
            code = (
                f'<strong style="font-size:16px;">{b.checkin_code}</strong>'
                if b.checkin_code
                else '<span style="color:#767676;">code in its own mail</span>'
            )
            parts.append(
                f"<tr>"
                f'<td style="{css_cell}white-space:nowrap;font-weight:600;">'
                f"{b.start:%H:%M}&ndash;{b.end:%H:%M}</td>"
                f'<td style="{css_cell}">{room}'
                f'<div style="color:#595959;font-size:13px;">{building}</div></td>'
                f'<td style="{css_cell}text-align:right;white-space:nowrap;">{code}</td>'
                f"</tr>"
            )
        parts.append("</table>")

    total = sum(int((b.end - b.start).total_seconds() // 60) for b in bookings)
    parts.append(
        f'<p style="margin-top:24px;font-size:15px;">'
        f"{len(bookings)} bookings, {total / 60:.1f} hours in total.</p>"
    )
    parts.append(
        '<p style="font-size:14px;color:#595959;">Each one is released if nobody checks '
        "in within 15 minutes of the start.</p></div>"
    )
    return "".join(parts)


def digest(cfg: Config, bookings: list[HeldBooking], places: dict[int, str]) -> bool:
    if not bookings:
        return False
    first = min(b.day for b in bookings)
    last = max(b.day for b in bookings)
    subject = f"uct: {len(bookings)} bookings, {first:%d %b} to {last:%d %b}"
    return send(cfg, subject, schedule(bookings, places), as_html(bookings, places))


def alert(cfg: Config, subject: str, body: str) -> bool:
    return send(cfg, f"uct: {subject}", body)
