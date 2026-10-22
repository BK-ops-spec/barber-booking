import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from barber import availability as avail
from barber import booking as booking_service
from barber import db
from barber import settings as shop_settings
from barber.booking import BookingError, BookingRequest
from barber.timeutil import get_zone, local_to_utc, to_utc_iso

ZONE_NAME = "UTC"


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.path)
        db.initialize(self.conn)
        shop_settings.set_value(self.conn, "shop_timezone", ZONE_NAME)
        shop_settings.set_value(self.conn, "lead_time_min", "0")
        shop_settings.set_value(self.conn, "slot_interval_min", "15")
        for key in ("open_time", "close_time"):
            shop_settings.set_value(self.conn, key, "09:00" if key == "open_time" else "17:00")
        self.conn.execute("DELETE FROM shop_hours")
        for day in range(7):
            if day == 6:
                continue
            self.conn.execute(
                "INSERT INTO shop_hours(day_of_week, start_time, end_time) VALUES(?, '09:00', '17:00')",
                (day,),
            )
        self.conn.execute("DELETE FROM staff_hours")
        self.conn.execute(
            "INSERT INTO staff_hours(staff_id, day_of_week, start_time, end_time) VALUES(1, 1, '09:00', '17:00')"
        )
        self.conn.execute(
            "INSERT INTO staff_hours(staff_id, day_of_week, start_time, end_time) VALUES(2, 1, '09:00', '17:00')"
        )
        self.conn.execute(
            "INSERT INTO staff_hours(staff_id, day_of_week, start_time, end_time) VALUES(3, 1, '10:00', '14:00')"
        )
        self.service_id = 1
        self.duration = self.conn.execute(
            "SELECT duration_min FROM services WHERE id = 1"
        ).fetchone()["duration_min"]
        self.day = self.next_weekday()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def next_weekday(self, weekday: int = 1):
        base = datetime.now(get_zone(ZONE_NAME)).date()
        for offset in range(1, 15):
            candidate = base + timedelta(days=offset)
            if candidate.weekday() == weekday:
                return candidate
        raise AssertionError("no weekday found")

    def make_request(self, **kwargs) -> BookingRequest:
        payload = {
            "service_id": self.service_id,
            "day": self.day,
            "start_local": "10:00",
            "customer_name": "Alex Doe",
            "phone": "555 123 4567",
            "staff_id": 1,
        }
        payload.update(kwargs)
        return BookingRequest(**payload)


class AvailabilityTests(BaseCase):
    def test_slot_grid_respects_duration_and_hours(self):
        result = avail.availability_for_date(self.conn, self.day, self.service_id, 1)
        self.assertTrue(result.slots)
        self.assertIn("09:00", result.slots)
        last = result.slots[-1]
        self.assertLessEqual(int(last[:2]) * 60 + int(last[3:]), 17 * 60 - self.duration)

    def test_shorter_service_fits_more_slots(self):
        short = self.conn.execute("SELECT id FROM services WHERE duration_min = 30 LIMIT 1").fetchone()["id"]
        long_slots = avail.availability_for_date(self.conn, self.day, self.service_id, 1).slots
        short_slots = avail.availability_for_date(self.conn, self.day, short, 1).slots
        self.assertGreater(len(short_slots), len(long_slots))

    def test_staff_specific_hours_narrow_the_window(self):
        result = avail.availability_for_date(self.conn, self.day, self.service_id, 3)
        self.assertTrue(result.slots)
        self.assertGreaterEqual(result.slots[0], "10:00")
        self.assertLessEqual(result.slots[-1], "14:00")

    def test_service_not_offered_by_staff(self):
        service_id = self.conn.execute(
            "SELECT service_id FROM staff_services WHERE staff_id = 1 AND service_id NOT IN "
            "(SELECT service_id FROM staff_services WHERE staff_id = 3) LIMIT 1"
        ).fetchone()["service_id"]
        result = avail.availability_for_date(self.conn, self.day, service_id, 3)
        self.assertTrue(result.closed)
        self.assertEqual(result.reason, "Barber does not offer this service")

    def test_closed_day_blocks_availability(self):
        self.conn.execute("INSERT INTO closures(day, reason) VALUES(?, 'Holiday')", (self.day.isoformat(),))
        result = avail.availability_for_date(self.conn, self.day, self.service_id, 1)
        self.assertTrue(result.closed)
        self.assertEqual(result.slots, [])

    def test_any_staff_union_is_wider_than_single_barber(self):
        union = avail.availability_for_date(self.conn, self.day, self.service_id).slots
        single = avail.availability_for_date(self.conn, self.day, self.service_id, 3).slots
        self.assertGreater(len(union), len(single))


class BookingTests(BaseCase):
    def test_create_booking_returns_code_and_normalised_fields(self):
        appointment = booking_service.create_booking(self.conn, self.make_request())
        self.assertEqual(len(appointment["code"]), 6)
        self.assertEqual(appointment["phone"], "5551234567")
        self.assertEqual(appointment["status"], "confirmed")
        self.assertEqual(appointment["staff_id"], 1)

    def test_double_booking_is_rejected(self):
        booking_service.create_booking(self.conn, self.make_request())
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request(customer_name="Second Guy"))
        self.assertIn("just taken", ctx.exception.message)

    def test_overlapping_but_not_identical_time_is_rejected(self):
        booking_service.create_booking(self.conn, self.make_request(start_local="10:00"))
        with self.assertRaises(BookingError):
            booking_service.create_booking(self.conn, self.make_request(start_local="10:15"))

    def test_adjacent_booking_is_allowed(self):
        booking_service.create_booking(self.conn, self.make_request(start_local="10:00"))
        appointment = booking_service.create_booking(
            self.conn, self.make_request(start_local="10:45", customer_name="Later Guy")
        )
        self.assertEqual(appointment["status"], "confirmed")

    def test_different_barbers_can_take_same_slot(self):
        booking_service.create_booking(self.conn, self.make_request(staff_id=1))
        appointment = booking_service.create_booking(
            self.conn, self.make_request(staff_id=2, customer_name="Other Guy")
        )
        self.assertEqual(appointment["staff_id"], 2)

    def test_any_staff_assigns_a_free_barber(self):
        appointment = booking_service.create_booking(self.conn, self.make_request(staff_id=None))
        self.assertIn(appointment["staff_id"], (1, 2, 3))

    def test_out_of_hours_is_rejected(self):
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request(start_local="20:00"))
        self.assertIn("working hours", ctx.exception.message)

    def test_past_date_is_rejected(self):
        past = self.day - timedelta(days=30)
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request(day=past))
        self.assertIn("past", ctx.exception.message)

    def test_beyond_booking_window_is_rejected(self):
        far = self.day + timedelta(days=400)
        with self.assertRaises(BookingError):
            booking_service.create_booking(self.conn, self.make_request(day=far))

    def test_closed_day_is_rejected(self):
        self.conn.execute("INSERT INTO closures(day, reason) VALUES(?, 'Holiday')", (self.day.isoformat(),))
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request())
        self.assertIn("closed", ctx.exception.message)

    def test_service_not_offered_by_chosen_barber(self):
        offered = {
            r["service_id"]
            for r in self.conn.execute(
                "SELECT service_id FROM staff_services WHERE staff_id = 2"
            ).fetchall()
        }
        missing = self.conn.execute(
            "SELECT id FROM services WHERE id NOT IN "
            "(SELECT service_id FROM staff_services WHERE staff_id = 2) LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(missing)
        self.assertNotIn(missing["id"], offered)
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(
                self.conn, self.make_request(staff_id=2, service_id=missing["id"])
            )
        self.assertIn("does not offer", ctx.exception.message)

    def test_lead_time_enforced(self):
        shop_settings.set_value(self.conn, "lead_time_min", "180")
        today = datetime.now(get_zone(ZONE_NAME)).date()
        if today.weekday() != 1:
            self.skipTest("lead time only testable on a working weekday")
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request(day=today, start_local="23:00"))
        self.assertIn("notice", ctx.exception.message)

    def test_invalid_phone_is_rejected(self):
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request(phone="12"))
        self.assertEqual(ctx.exception.field, "phone")

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(BookingError) as ctx:
            booking_service.create_booking(self.conn, self.make_request(email="not-an-email"))
        self.assertEqual(ctx.exception.field, "email")

    def test_cancel_frees_the_slot(self):
        appointment = booking_service.create_booking(self.conn, self.make_request())
        booking_service.cancel_appointment(self.conn, appointment["id"])
        second = booking_service.create_booking(
            self.conn, self.make_request(customer_name="Second Guy")
        )
        self.assertEqual(second["status"], "confirmed")

    def test_lookup_by_code_and_phone(self):
        appointment = booking_service.create_booking(self.conn, self.make_request())
        found = booking_service.find_by_code_and_phone(self.conn, appointment["code"], "5551234567")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], appointment["id"])
        self.assertIsNone(booking_service.find_by_code_and_phone(self.conn, appointment["code"], "9999999999"))

    def test_reschedule_moves_appointment(self):
        appointment = booking_service.create_booking(self.conn, self.make_request())
        updated = booking_service.reschedule(self.conn, appointment["id"], self.day, "15:00")
        self.assertEqual(updated["local_time"], "15:00")

    def test_reschedule_to_taken_slot_fails(self):
        first = booking_service.create_booking(self.conn, self.make_request())
        booking_service.create_booking(
            self.conn, self.make_request(start_local="15:00", customer_name="Other Guy")
        )
        with self.assertRaises(BookingError):
            booking_service.reschedule(self.conn, first["id"], self.day, "15:00")

    def test_booked_slot_disappears_from_availability(self):
        booking_service.create_booking(self.conn, self.make_request(start_local="11:00"))
        slots = avail.availability_for_date(self.conn, self.day, self.service_id, 1).slots
        self.assertNotIn("11:00", slots)

    def test_end_time_matches_duration(self):
        appointment = booking_service.create_booking(self.conn, self.make_request(start_local="10:00"))
        start = datetime.fromisoformat(appointment["start_utc"])
        end = datetime.fromisoformat(appointment["end_utc"])
        self.assertEqual((end - start).total_seconds() / 60, self.duration)

    def test_unique_codes_across_bookings(self):
        codes = set()
        for index in range(8):
            appointment = booking_service.create_booking(
                self.conn,
                self.make_request(start_local=f"{9 + index:02d}:00", customer_name=f"Guest {index}"),
            )
            codes.add(appointment["code"])
        self.assertEqual(len(codes), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
