import os
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from barber import certs, config, server


class CertificateTests(unittest.TestCase):
    def test_generates_a_usable_certificate_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert_path = Path(tmp) / "test.crt"
            key_path = Path(tmp) / "test.key"
            certs.generate_self_signed(cert_path, key_path, hostnames=["localhost", "127.0.0.1"])

            self.assertTrue(cert_path.is_file())
            self.assertTrue(key_path.is_file())

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert_path), str(key_path))

    def test_private_key_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert_path = Path(tmp) / "test.crt"
            key_path = Path(tmp) / "test.key"
            certs.generate_self_signed(cert_path, key_path, hostnames=["localhost"])
            mode = key_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_certificate_covers_requested_hosts(self):
        try:
            from cryptography import x509
        except ImportError:
            self.skipTest("cryptography not installed")
        with tempfile.TemporaryDirectory() as tmp:
            cert_path = Path(tmp) / "test.crt"
            key_path = Path(tmp) / "test.key"
            certs.generate_self_signed(
                cert_path, key_path, hostnames=["localhost", "127.0.0.1", "shop.example.com"]
            )
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = set()
            for value in sans:
                if isinstance(value, x509.DNSName):
                    names.add(value.value)
                elif isinstance(value, x509.IPAddress):
                    names.add(str(value.value))
            self.assertIn("localhost", names)
            self.assertIn("127.0.0.1", names)
            self.assertIn("shop.example.com", names)

    def test_missing_certificate_raises_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                server._wrap_tls(
                    socket.socket(), Path(tmp) / "nope.crt", Path(tmp) / "nope.key"
                )


class TlsServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cert_path = Path(self.tmp.name) / "srv.crt"
        key_path = Path(self.tmp.name) / "srv.key"
        certs.generate_self_signed(cert_path, key_path, hostnames=["localhost", "127.0.0.1"])
        self.db_path = Path(self.tmp.name) / "tls.db"
        original_secure = config.SESSION_COOKIE_SECURE
        self.original_secure = original_secure
        self.server = server.create_server(
            "127.0.0.1", 0, self.db_path, tls_cert=cert_path, tls_key=key_path
        )
        self.port = self.server.server_address[1]
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        config.SESSION_COOKIE_SECURE = self.original_secure
        self.tmp.cleanup()

    def _https_request(self, path="/api/config", method="GET", payload=None):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        url = f"https://127.0.0.1:{self.port}{path}"
        data = None
        headers = {}
        if payload is not None:
            import json

            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            return response.status, response.read(), response.headers

    def test_serves_over_tls(self):
        status, body, headers = self._https_request()
        self.assertEqual(status, 200)
        self.assertIn(b"shop_name", body)

    def test_secure_cookies_are_enabled_when_tls_is_on(self):
        self.assertTrue(config.SESSION_COOKIE_SECURE)

    def test_hsts_is_sent_over_tls(self):
        status, body, headers = self._https_request()
        self.assertIn("max-age=31536000", headers.get("Strict-Transport-Security", ""))

    def test_session_cookie_gets_the_secure_flag_over_tls(self):
        status, body, headers = self._https_request(
            "/api/admin/login",
            "POST",
            {"username": "admin", "password": "admin123"},
        )
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_plain_http_cannot_talk_to_a_tls_server(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/config")
        with self.assertRaises((urllib.error.URLError, ConnectionError, OSError)):
            urllib.request.urlopen(req, timeout=5)


class RedirectServerTests(unittest.TestCase):
    def test_http_redirects_to_https(self):
        import threading

        redirect = server.start_redirect_server("127.0.0.1", 0)
        port = redirect.server_address[1]
        try:

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None

            opener = urllib.request.build_opener(NoRedirect)
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/config",
                headers={"Host": "booking.example.com"},
            )
            try:
                opener.open(req, timeout=5)
                self.fail("expected a redirect")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 308)
                self.assertEqual(
                    exc.headers.get("Location"), "https://booking.example.com/api/config"
                )
        finally:
            redirect.shutdown()
            redirect.server_close()


class MakeCertCommandTests(unittest.TestCase):
    def test_make_cert_command_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent.parent / "run.py"),
                    "--make-cert",
                    "localhost",
                    "--cert-dir",
                    tmp,
                ],
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue((Path(tmp) / "localhost.crt").is_file())
            self.assertTrue((Path(tmp) / "localhost.key").is_file())

    def test_tls_without_cert_exits_with_an_error(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py"), "--tls"],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--cert", result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
