import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from barber import api, auth, config, db, server, settings as shop_settings
from barber.ratelimit import RateLimiter, RateLimitExceeded

from test_api import ApiTestCase


class CsrfProtectionTests(ApiTestCase):
    def test_login_returns_csrf_token(self):
        status, body = self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "admin123"}, expect=200
        )
        self.assertTrue(body.get("csrf_token"))
        self.assertEqual(len(body["csrf_token"]), 64)

    def test_me_endpoint_returns_csrf_token(self):
        self.login()
        status, body = self.request("GET", "/api/admin/me", expect=200)
        self.assertTrue(body.get("csrf_token"))

    def test_admin_mutation_without_csrf_header_is_rejected(self):
        self.login()
        self.csrf_token = ""
        self.request("POST", "/api/admin/logout", {}, expect=403)

    def test_admin_mutation_with_wrong_csrf_header_is_rejected(self):
        self.login()
        self.csrf_token = "0" * 64
        self.request("POST", "/api/admin/logout", {}, expect=403)

    def test_admin_mutation_with_valid_csrf_header_succeeds(self):
        self.login()
        self.request("POST", "/api/admin/logout", {}, expect=200)

    def test_csrf_token_is_bound_to_the_session(self):
        self.login()
        first = self.csrf_token
        self.request("POST", "/api/admin/logout", {}, expect=200)
        self.csrf_token = ""
        self.request("POST", "/api/admin/login", {"username": "admin", "password": "admin123"}, expect=200)
        self.assertNotEqual(first, self.csrf_token)

    def test_csrf_token_cannot_be_forged_from_the_session_cookie(self):
        self.login()
        self.csrf_token = ""
        self.request("POST", "/api/admin/logout", {}, expect=403)
        self.csrf_token = auth.csrf_token(self.cookies.get(config.SESSION_COOKIE))
        self.request("POST", "/api/admin/logout", {}, expect=200)

    def test_public_booking_endpoints_do_not_require_csrf(self):
        self.csrf_token = ""
        status, body = self.request("GET", "/api/config", expect=200)
        self.assertTrue(body["services"])

    def test_get_requests_do_not_require_csrf(self):
        self.login()
        self.csrf_token = ""
        self.request("GET", "/api/admin/appointments", expect=200)


class SessionCookieTests(ApiTestCase):
    def _login_headers(self):
        req = urllib.request.Request(
            self.base + "/api/admin/login",
            data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as exc:
            return exc.headers.get_all("Set-Cookie") or []

    def test_session_cookie_is_httponly_and_samesite_strict(self):
        cookies = self._login_headers()
        self.assertTrue(cookies)
        joined = " ".join(cookies)
        self.assertIn("HttpOnly", joined)
        self.assertIn("SameSite=Strict", joined)
        self.assertIn("Path=/", joined)

    def test_session_cookie_is_not_readable_from_javascript(self):
        cookies = self._login_headers()
        self.assertTrue(all("HttpOnly" in c for c in cookies))


class SecurityHeaderTests(ApiTestCase):
    def _headers(self, path):
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.headers

    def test_html_response_has_security_headers(self):
        headers = self._headers("/")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("geolocation=()", headers["Permissions-Policy"])

    def test_api_response_has_security_headers(self):
        headers = self._headers("/api/config")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_server_header_does_not_leak_python_version(self):
        headers = self._headers("/")
        self.assertNotIn("Python", headers.get("Server", ""))


class RequestLimitTests(ApiTestCase):
    def test_oversized_body_is_rejected(self):
        payload = json.dumps({"customer_name": "x" * 200000}).encode()
        req = urllib.request.Request(
            self.base + "/api/bookings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                self.fail(f"expected failure, got {response.status}")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 413)

    def test_booking_rate_limit_returns_429_with_retry_after(self):
        original = api._booking_limiter
        api._booking_limiter = RateLimiter(limit=1, window_seconds=300)
        try:
            day = self.bookable_day()
            payload = {
                "service_id": 1,
                "staff_id": 1,
                "date": day,
                "time": "10:00",
                "customer_name": "Rate Test",
                "phone": "5551112222",
            }
            self.request("POST", "/api/bookings", payload, expect=201)
            status, body = self.request(
                "POST", "/api/bookings", dict(payload, time="11:00"), expect=429
            )
            self.assertIn("Too many", body["error"])
        finally:
            api._booking_limiter = original


class StaticFileTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.secret = config.STATIC_DIR / ".env"
        self.secret.write_text("SECRET=should-never-be-served\n")
        self.binary = config.STATIC_DIR / "secret.bin"
        self.binary.write_bytes(b"\x00\x01\x02")

    def tearDown(self):
        for path in (self.secret, self.binary):
            try:
                path.unlink()
            except OSError:
                pass
        super().tearDown()

    def _get(self, path):
        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_disallowed_extension_is_not_served(self):
        status, body = self._get("/.env")
        self.assertEqual(status, 404)
        self.assertNotIn(b"SECRET", body)

    def test_unknown_binary_extension_is_not_served(self):
        status, body = self._get("/secret.bin")
        self.assertEqual(status, 404)

    def test_path_traversal_is_blocked(self):
        status, body = self._get("/../barber/config.py")
        self.assertEqual(status, 404)
        self.assertNotIn(b"DB_PATH", body)

    def test_encoded_path_traversal_is_blocked(self):
        status, body = self._get("/%2e%2e/barber/config.py")
        self.assertEqual(status, 404)


class DatabasePermissionTests(unittest.TestCase):
    def test_database_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "perm.db"
            conn = db.connect(path)
            db.initialize(conn)
            conn.close()
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)


class RateLimiterTests(unittest.TestCase):
    def test_limit_blocks_after_threshold(self):
        limiter = RateLimiter(limit=2, window_seconds=60)
        limiter.hit("k")
        limiter.hit("k")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("k")

    def test_reset_clears_counter(self):
        limiter = RateLimiter(limit=1, window_seconds=60)
        limiter.hit("k")
        limiter.reset("k")
        limiter.check("k")

    def test_keys_are_independent(self):
        limiter = RateLimiter(limit=1, window_seconds=60)
        limiter.hit("a")
        limiter.check("b")

    def test_retry_after_is_positive_when_blocked(self):
        limiter = RateLimiter(limit=1, window_seconds=60)
        limiter.hit("k")
        self.assertGreaterEqual(limiter.retry_after("k"), 1)


class AdminAccessTests(ApiTestCase):
    def test_admin_endpoints_require_session(self):
        for path in ("/api/admin/appointments", "/api/admin/services", "/api/admin/stats"):
            self.request("GET", path, expect=401)

    def test_admin_endpoints_reject_garbage_session_cookie(self):
        self.cookies[config.SESSION_COOKIE] = "not-a-real-token"
        self.request("GET", "/api/admin/appointments", expect=401)

    def test_booking_lookup_requires_code_and_phone(self):
        self.request("GET", "/api/bookings/lookup?code=ABC123", expect=400)
        self.request("GET", "/api/bookings/lookup?code=ABC123&phone=5551112222", expect=404)


if __name__ == "__main__":
    unittest.main()
