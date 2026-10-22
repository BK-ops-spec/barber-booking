from __future__ import annotations

import json
import mimetypes
import posixpath
import socket
import sqlite3
import ssl
import sys
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import api
from . import config
from . import db
from .api import ApiError, Request, Response

PAGE_ROUTES = {
    "/": "index.html",
    "/book": "index.html",
    "/admin": "admin.html",
    "/admin/": "admin.html",
}

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")

MAX_BODY_BYTES = 64 * 1024

ALLOWED_STATIC_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".txt", ".webmanifest",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2",
}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class RequestTooLarge(Exception):
    pass


class ConnectionPool:
    def __init__(self, path: Path):
        self._path = path
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = db.connect(self._path)
            self._local.conn = conn
        return conn

    def close_all(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


class Handler(BaseHTTPRequestHandler):
    server_version = "BarberShop"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def pool(self) -> ConnectionPool:
        return self.server.pool  # type: ignore[attr-defined]

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else ""

    def _apply_security_headers(self) -> None:
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        if config.SESSION_COOKIE_SECURE:
            self.send_header(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_HEAD(self) -> None:
        self._handle("GET", head_only=True)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def _handle(self, method: str, head_only: bool = False) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if not path.startswith("/api/"):
            self._serve_static(path, head_only=head_only)
            return

        try:
            body = self._read_body()
        except RequestTooLarge:
            self._send_json(
                Response(status=413, data={"error": "Request body is too large"}), head_only=head_only
            )
            return
        cookies = self._parse_cookies()
        conn = self.pool.connection()
        request = Request(
            method=method,
            path=path,
            conn=conn,
            query=query,
            body=body,
            cookies=cookies,
            headers={k.lower(): v for k, v in self.headers.items()},
            client_ip=self._client_ip(),
        )
        try:
            response = api.dispatch(conn, request)
        except ApiError as exc:
            response = Response(status=exc.status, data={"error": exc.message, "field": exc.field_name})
            if exc.retry_after:
                response.headers["Retry-After"] = str(exc.retry_after)
        except Exception:
            traceback.print_exc()
            response = Response(status=500, data={"error": "Something went wrong on our end"})

        self._send_json(response, head_only=head_only)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise RequestTooLarge()
        raw = self.rfile.read(length)
        if not raw:
            return {}
        content_type = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type or raw[:1] in (b"{", b"["):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        if "application/x-www-form-urlencoded" in content_type:
            parsed = parse_qs(raw.decode("utf-8"))
            return {k: v[0] for k, v in parsed.items()}
        return {}

    def _parse_cookies(self) -> dict[str, str]:
        raw = self.headers.get("Cookie")
        if not raw:
            return {}
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return {}
        return {key: morsel.value for key, morsel in jar.items()}

    def _serve_static(self, path: str, head_only: bool = False) -> None:
        if path in PAGE_ROUTES:
            target = config.STATIC_DIR / PAGE_ROUTES[path]
        else:
            relative = posixpath.normpath(path.lstrip("/"))
            if relative.startswith("..") or relative in {".", "/"}:
                self._send_error_page(404)
                return
            target = config.STATIC_DIR / relative
        try:
            target = target.resolve()
            target.relative_to(config.STATIC_DIR.resolve())
        except (ValueError, OSError):
            self._send_error_page(404)
            return
        if target.suffix.lower() not in ALLOWED_STATIC_SUFFIXES:
            self._send_error_page(404)
            return
        if not target.is_file():
            self._send_error_page(404)
            return

        data = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "image/svg+xml",
        }:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._apply_security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _send_error_page(self, status: int) -> None:
        body = b"<!doctype html><title>Not found</title><h1>404 - Not found</h1>"
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, response: Response, head_only: bool = False) -> None:
        payload = json.dumps(response.data, default=str).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for cookie in response.cookies:
            self.send_header("Set-Cookie", cookie)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self._apply_security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, pool: ConnectionPool):
        super().__init__(address, handler)
        self.pool = pool

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


def create_server(
    host: str = config.HOST,
    port: int = config.PORT,
    db_path: Path | None = None,
    tls_cert: Path | str | None = None,
    tls_key: Path | str | None = None,
) -> Server:
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.initialize(conn)
    finally:
        conn.close()
    pool = ConnectionPool(path)
    server = Server((host, port), Handler, pool)
    if tls_cert and tls_key:
        server.socket = _wrap_tls(server.socket, Path(tls_cert), Path(tls_key))
        config.SESSION_COOKIE_SECURE = True
    return server


def _wrap_tls(sock, cert_path: Path, key_path: Path):
    if not cert_path.is_file():
        raise FileNotFoundError(f"certificate not found: {cert_path}")
    if not key_path.is_file():
        raise FileNotFoundError(f"private key not found: {key_path}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5")
    return context.wrap_socket(sock, server_side=True)


class RedirectHandler(BaseHTTPRequestHandler):
    server_version = "BarberShop"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def _redirect(self) -> None:
        host = self.headers.get("Host") or ""
        target = f"https://{host}{self.path}"
        self.send_response(308)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.end_headers()

    def do_GET(self) -> None:
        self._redirect()

    def do_HEAD(self) -> None:
        self._redirect()

    def do_POST(self) -> None:
        self._redirect()

    def do_PUT(self) -> None:
        self._redirect()

    def do_PATCH(self) -> None:
        self._redirect()

    def do_DELETE(self) -> None:
        self._redirect()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def start_redirect_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), RedirectHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(
    argv: list[str] | None = None,
    tls_cert: Path | str | None = None,
    tls_key: Path | str | None = None,
    redirect_port: int = 0,
) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    host, port = config.HOST, config.PORT
    if argv:
        host = argv[0]
    if len(argv) > 1:
        port = int(argv[1])

    server = create_server(host, port, tls_cert=tls_cert, tls_key=tls_key)
    scheme = "https" if (tls_cert and tls_key) else "http"
    if redirect_port:
        start_redirect_server("0.0.0.0", redirect_port)
        print(f"HTTP redirect listener on port {redirect_port} -> {scheme}://")
    print(f"Barber shop booking server running on {scheme}://{host}:{port}")
    print(f"Admin panel: {scheme}://{host}:{port}/admin")
    print(f"Database: {config.DB_PATH}")
    if scheme == "https":
        print("Secure cookies and HSTS are enabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
