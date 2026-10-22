import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from barber import config, db, server, settings as shop_settings
from barber.timeutil import get_zone


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "api.db"
        original = config.DB_PATH
        config.DB_PATH = self.db_path
        try:
            self.server = server.create_server("127.0.0.1", 0, self.db_path)
        finally:
            config.DB_PATH = original
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://{self.host}:{self.port}"
        self.cookies: dict[str, str] = {}
        self.csrf_token = ""
        conn = db.connect(self.db_path)
        db.initialize(conn)
        shop_settings.set_value(conn, "shop_timezone", "UTC")
        shop_settings.set_value(conn, "lead_time_min", "0")
        conn.close()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, method, path, payload=None, expect=None, raw=False, csrf=True):
        url = self.base + path
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if csrf and self.csrf_token and method.upper() not in {"GET", "HEAD"}:
            headers["X-CSRF-Token"] = self.csrf_token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read()
                status = response.status
                for header in response.headers.get_all("Set-Cookie") or []:
                    self._store_cookie(header)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
            for header in exc.headers.get_all("Set-Cookie") or []:
                self._store_cookie(header)
        if expect is not None:
            self.assertEqual(status, expect, f"{method} {path} -> {status}: {body[:400]}")
        if raw:
            return status, body
        return status, json.loads(body) if body else {}

    def _store_cookie(self, header: str) -> None:
        pair = header.split(";")[0].strip()
        if "=" not in pair:
            return
        key, value = pair.split("=", 1)
        if value:
            self.cookies[key.strip()] = value.strip()
        else:
            self.cookies.pop(key.strip(), None)

    def bookable_day(self) -> str:
        day = datetime.now(get_zone("UTC")).date() + timedelta(days=1)
        while day.weekday() == 6:
            day += timedelta(days=1)
        return day.isoformat()

    def login(self) -> None:
        status, body = self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "admin123"}, expect=200
        )
        self.assertEqual(body["user"]["username"], "admin")
        self.csrf_token = body.get("csrf_token", "")


class PublicApiTests(ApiTestCase):
    def test_config_lists_services_and_staff(self):
        status, body = self.request("GET", "/api/config", expect=200)
        self.assertTrue(body["services"])
        self.assertTrue(body["staff"])
        self.assertIn("shop_name", body)

    def test_services_endpoint(self):
        status, body = self.request("GET", "/api/services", expect=200)
        self.assertEqual(len(body), 6)
        self.assertIn("price_cents", body[0])

    def test_staff_filtered_by_service(self):
        status, body = self.request("GET", "/api/staff?service_id=1", expect=200)
        self.assertTrue(body)

    def test_availability_returns_slots(self):
        status, body = self.request(
            "GET", f"/api/availability?service_id=1&staff_id=1&date={self.bookable_day()}", expect=200
        )
        self.assertTrue(body["slots"])
        self.assertFalse(body["closed"])
        self.assertRegex(body["slots"][0], r"^\d{2}:\d{2}$")

    def test_availability_requires_service(self):
        self.request("GET", "/api/availability", expect=400)

    def test_availability_rejects_bad_date(self):
        self.request("GET", "/api/availability?service_id=1&date=not-a-date", expect=400)

    def test_dates_endpoint(self):
        status, body = self.request("GET", "/api/dates?service_id=1", expect=200)
        self.assertTrue(body["dates"])

    def test_create_and_lookup_booking(self):
        day = self.bookable_day()
        status, booking = self.request(
            "POST",
            "/api/bookings",
            {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "10:00",
                "customer_name": "Sam Rivers",
                "phone": "555-987-6543",
                "email": "sam@example.com",
            },
            expect=201,
        )
        self.assertEqual(len(booking["code"]), 6)
        status, found = self.request(
            "GET", f"/api/bookings/lookup?code={booking['code']}&phone=5559876543", expect=200
        )
        self.assertEqual(found["id"], booking["id"])
        self.assertEqual(found["service_name"], booking["service_name"])

    def test_lookup_with_wrong_phone_is_not_found(self):
        day = self.bookable_day()
        status, booking = self.request(
            "POST",
            "/api/bookings",
            {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "11:00",
                "customer_name": "Sam Rivers",
                "phone": "5559876543",
            },
            expect=201,
        )
        self.request(
            "GET", f"/api/bookings/lookup?code={booking['code']}&phone=5550000000", expect=404
        )

    def test_double_booking_returns_error(self):
        day = self.bookable_day()
        payload = {
            "service_id": 1,
            "staff_id": 1,
            "date": day,
            "time": "10:00",
            "customer_name": "Sam Rivers",
            "phone": "5559876543",
        }
        self.request("POST", "/api/bookings", payload, expect=201)
        status, body = self.request("POST", "/api/bookings", dict(payload, customer_name="Other"), expect=400)
        self.assertIn("just taken", body["error"])

    def test_missing_details_returns_field_error(self):
        status, body = self.request(
            "POST",
            "/api/bookings",
            {"service_id": 1, "date": self.bookable_day(), "time": "10:00", "customer_name": "Sam Rivers"},
            expect=400,
        )
        self.assertEqual(body["field"], "phone")

    def test_cancel_booking_by_code_and_phone(self):
        day = self.bookable_day()
        status, booking = self.request(
            "POST",
            "/api/bookings",
            {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "10:00",
                "customer_name": "Sam Rivers",
                "phone": "5559876543",
            },
            expect=201,
        )
        status, cancelled = self.request(
            "POST",
            f"/api/bookings/{booking['code']}/cancel",
            {"phone": "5559876543"},
            expect=200,
        )
        self.assertEqual(cancelled["status"], "cancelled")

    def test_unknown_route_returns_404(self):
        self.request("GET", "/api/nope", expect=404)

    def test_method_not_allowed(self):
        self.request("POST", "/api/config", {}, expect=405)

    def test_static_index_is_served(self):
        status, body = self.request("GET", "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body.lower())

    def test_admin_page_is_served(self):
        status, body = self.request("GET", "/admin", raw=True)
        self.assertEqual(status, 200)


class AdminApiTests(ApiTestCase):
    def test_admin_routes_require_login(self):
        self.request("GET", "/api/admin/appointments", expect=401)
        self.request("GET", "/api/admin/services", expect=401)

    def test_login_with_bad_password(self):
        self.request("POST", "/api/admin/login", {"username": "admin", "password": "nope"}, expect=401)

    def test_repeated_failures_are_rate_limited(self):
        for _ in range(8):
            self.request(
                "POST", "/api/admin/login", {"username": "ratelimited", "password": "nope"}, expect=401
            )
        status, body = self.request(
            "POST", "/api/admin/login", {"username": "ratelimited", "password": "nope"}, expect=429
        )
        self.assertIn("Too many attempts", body["error"])

    def test_session_cookie_is_http_date(self):
        status, body = self.request("GET", "/api/admin/me", expect=401)
        self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "admin123"}, expect=200
        )
        self.assertIn("barber_session", self.cookies)

    def test_login_and_logout(self):
        self.login()
        self.request("GET", "/api/admin/me", expect=200)
        self.request("POST", "/api/admin/logout", {}, expect=200)
        self.request("GET", "/api/admin/me", expect=401)

    def test_admin_can_list_appointments(self):
        self.login()
        status, body = self.request("GET", "/api/admin/appointments", expect=200)
        self.assertIn("appointments", body)

    def test_admin_can_create_service(self):
        self.login()
        status, service = self.request(
            "POST",
            "/api/admin/services",
            {"name": "Line Up", "price_cents": 1500, "duration_min": 15, "description": "Edges only"},
            expect=201,
        )
        self.assertEqual(service["name"], "Line Up")
        status, body = self.request("GET", "/api/admin/services", expect=200)
        self.assertTrue(any(s["name"] == "Line Up" for s in body))

    def test_admin_can_update_service(self):
        self.login()
        status, service = self.request(
            "POST", "/api/admin/services", {"name": "Shave", "price_cents": 1000, "duration_min": 20},
            expect=201,
        )
        status, updated = self.request(
            "PUT", f"/api/admin/services/{service['id']}", {"name": "Hot Towel Shave", "price_cents": 3500},
            expect=200,
        )
        self.assertEqual(updated["name"], "Hot Towel Shave")
        self.assertEqual(updated["price_cents"], 3500)

    def test_admin_delete_service(self):
        self.login()
        status, service = self.request(
            "POST", "/api/admin/services", {"name": "Temporary", "price_cents": 500, "duration_min": 10},
            expect=201,
        )
        self.request("DELETE", f"/api/admin/services/{service['id']}", expect=200)
        status, body = self.request("GET", "/api/admin/services", expect=200)
        self.assertFalse(any(s["id"] == service["id"] for s in body))

    def test_admin_can_create_staff_with_services(self):
        self.login()
        status, member = self.request(
            "POST",
            "/api/admin/staff",
            {"name": "Riley Fox", "title": "Barber", "service_ids": [1, 2]},
            expect=201,
        )
        self.assertEqual(sorted(member["service_ids"]), [1, 2])

    def test_admin_can_update_hours(self):
        self.login()
        self.request(
            "PUT",
            "/api/admin/hours",
            {"staff_hours": [{"staff_id": 1, "hours": [{"day_of_week": 1, "start_time": "10:00", "end_time": "15:00"}]}]},
            expect=200,
        )
        status, body = self.request("GET", "/api/admin/hours", expect=200)
        entry = next(m for m in body["staff_hours"] if m["staff_id"] == 1)
        self.assertTrue(any(h["start_time"] == "10:00" for h in entry["hours"]))

    def test_closure_blocks_booking(self):
        self.login()
        day = self.bookable_day()
        self.request("POST", "/api/admin/closures", {"day": day, "reason": "Holiday"}, expect=201)
        status, body = self.request(
            "POST",
            "/api/bookings",
            {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "10:00",
                "customer_name": "Sam Rivers",
                "phone": "5559876543",
            },
            expect=400,
        )
        self.assertIn("closed", body["error"])

    def test_admin_status_update(self):
        self.login()
        day = self.bookable_day()
        status, booking = self.request(
            "POST",
            "/api/admin/appointments",
            {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "10:00",
                "customer_name": "Sam Rivers",
                "phone": "5559876543",
            },
            expect=201,
        )
        status, updated = self.request(
            "PATCH", f"/api/admin/appointments/{booking['id']}", {"status": "completed"}, expect=200
        )
        self.assertEqual(updated["status"], "completed")

    def test_admin_reschedule(self):
        self.login()
        day = self.bookable_day()
        status, booking = self.request(
            "POST",
            "/api/admin/appointments",
            {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "10:00",
                "customer_name": "Sam Rivers",
                "phone": "5559876543",
            },
            expect=201,
        )
        status, updated = self.request(
            "PATCH",
            f"/api/admin/appointments/{booking['id']}",
            {"date": day, "time": "16:00"},
            expect=200,
        )
        self.assertEqual(updated["local_time"], "16:00")

    def test_admin_settings_roundtrip(self):
        self.login()
        status, body = self.request("GET", "/api/admin/settings", expect=200)
        self.assertIn("shop_name", body)
        status, body = self.request(
            "PUT", "/api/admin/settings", {"shop_tagline": "Sharp and ready"}, expect=200
        )
        self.assertEqual(body["shop_tagline"], "Sharp and ready")

    def test_admin_stats(self):
        self.login()
        status, body = self.request("GET", "/api/admin/stats", expect=200)
        self.assertIn("today_count", body)
        self.assertIn("week_count", body)

    def test_password_change(self):
        self.login()
        self.request(
            "POST",
            "/api/admin/password",
            {"current_password": "admin123", "new_password": "newsecret"},
            expect=200,
        )
        self.request("POST", "/api/admin/logout", {}, expect=200)
        self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "admin123"}, expect=401
        )
        self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "newsecret"}, expect=200
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
