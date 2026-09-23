from datetime import date, datetime
from zoneinfo import ZoneInfo

from uctbook.catalogue import Catalogue, Entry, Location, Seat
from uctbook.mailimport import adopt, apply, parse_when
from uctbook.state import Ledger

SAST = ZoneInfo("Africa/Johannesburg")

ROW = {
    "what": "KC01", "from": "8:00am", "to": "12:00pm",
    "when": "Monday 28 September 2026", "code": "2CG", "token": "3d03979feac4",
}


def catalogue():
    return Catalogue(
        locations=[Location(id=5154, name="Oppenheimer",
                            categories=[Entry(id=33215, name="Knowledge Commons Computers")])],
        seats=[Seat(id=165230, name="KC01", eid=131384, location=5154, category=33215)],
    )


def test_reads_the_mail_wording():
    assert parse_when("Monday 28 September 2026", "8:00am", SAST) == datetime(
        2026, 9, 28, 8, 0, tzinfo=SAST
    )


def test_adopts_a_booking_the_ledger_never_saw(tmp_path):
    led = Ledger(tmp_path / "s.json")
    assert adopt(led, [ROW], SAST, catalogue()) == 1
    held = led.bookings[0]
    assert held.checkin_code == "2CG"
    assert held.cancel_token == "3d03979feac4"
    assert held.day == date(2026, 9, 28)
    assert held.target == "Knowledge Commons Computers"


def test_does_not_adopt_the_same_booking_twice(tmp_path):
    led = Ledger(tmp_path / "s.json")
    adopt(led, [ROW], SAST, catalogue())
    assert adopt(led, [ROW], SAST, catalogue()) == 0


def test_fills_a_booking_already_held(tmp_path):
    led = Ledger(tmp_path / "s.json")
    adopt(led, [dict(ROW, code="", token="")], SAST, catalogue())
    assert apply(led, [ROW], SAST, {165230: "KC01"}) == 1
    assert led.bookings[0].checkin_code == "2CG"


def test_will_not_attach_a_mail_to_a_different_room(tmp_path):
    led = Ledger(tmp_path / "s.json")
    adopt(led, [dict(ROW, what="KC01", code="", token="")], SAST, catalogue())
    assert apply(led, [dict(ROW, what="Room 3")], SAST, {165230: "KC01"}) == 0
