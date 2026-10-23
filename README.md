# Sons of Detroit — Barber Shop Booking App

A complete booking system for a barbershop: customers pick a service, a barber and a time
slot; staff manage the book, the menu and the shop's opening hours from an admin panel.

Built with **zero runtime dependencies** — Python 3.11+ standard library (HTTP server +
SQLite) and vanilla JavaScript on the front end. Nothing to install, nothing to build.

```
python3 run.py
# → http://127.0.0.1:8000
```

---

## Features

**Customer booking flow**

- Service menu with pricing and duration
- Barber selection, including an "any available barber" option
- Live availability that updates as other customers book
- 4-step flow: service → barber → date & time → details
- Instant confirmation with a 6-character booking code
- Self-service lookup and cancellation with code + phone number
- Fully responsive, works on phones

**Staff portal** (`/admin`)

- Daily schedule with per-barber bookings
- Create bookings on behalf of walk-ins
- Reschedule, complete, cancel and restore appointments
- Manage services (price, duration, visibility)
- Manage barbers and which services each one offers
- Per-barber working hours plus shop-wide opening hours
- Closures for holidays
- Revenue and booking stats

**Scheduling engine**

- Working hours per barber, per weekday, falling back to shop hours
- Slot grid sized by service duration (a 90-minute service gets fewer slots than a 30-minute one)
- Lead time and maximum advance-booking window
- Timezone-aware: everything is stored in UTC, displayed in the shop's local timezone
- Atomic double-booking protection inside a database transaction

---

## Running it

```bash
python3 run.py                  # 127.0.0.1:8000
python3 run.py 0.0.0.0 5000     # custom host and port
```

Default admin login: **`<admin-username>`** / **`<admin-password>`** — change it under *Settings → Password* in the
staff portal, or set `BARBER_ADMIN_USER` / `BARBER_ADMIN_PASS` before first launch.

### HTTPS

The app can speak TLS directly, which is what you want for local testing or a small
internal deployment:

```bash
# 1. make a certificate (self-signed, browsers will warn until you trust it)
python3 run.py --make-cert booking.example.com

# 2. serve over HTTPS, plus an HTTP listener that redirects to it
python3 run.py 0.0.0.0 8443 --tls \
    --cert certs/booking.example.com.crt \
    --key  certs/booking.example.com.key \
    --redirect-http 8080
```

Enabling TLS automatically turns on the `Secure` cookie flag and `Strict-Transport-Security`,
so there is no separate `BARBER_SECURE_COOKIES` step to forget. TLS 1.2 is the floor and
only modern ciphers are offered.

### HTTPS in production (real certificate)

A self-signed certificate is not what the public should see. For a real deployment put a
TLS-terminating reverse proxy in front and let the app stay on plain HTTP on localhost —
the proxy is much better at TLS than the Python stdlib is.

**Caddy** (easiest — automatic Let's Encrypt certificates and renewal):

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the domain
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**nginx** (if you already run it):

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/barber-booking
sudo ln -s /etc/nginx/sites-available/barber-booking /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Full install:**

```bash
sudo ./deploy/setup.sh     # service account, /opt install, systemd unit
sudo systemctl start barber-booking
```

You need a real domain with an A/AAAA record pointing at the machine, and ports 80 and
443 open in the firewall. Let's Encrypt's HTTP-01 challenge uses port 80.

### A public HTTPS URL right now (no domain needed)

For a working public address without buying a domain, a Cloudflare quick tunnel gives
you a real, browser-trusted certificate immediately:

```bash
mkdir -p ~/.local/bin && cd ~/.local/bin
curl -sSLo cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared

./deploy/start-tunnel.sh          # starts the app and the tunnel
cat ~/.local/state/barber-booking/public-url
```

`deploy/barber-booking-tunnel.service` runs it as a systemd user service that restarts
on crash and comes back after a reboot (linger is enabled). The URL is random and
changes whenever the tunnel restarts — fine for testing, not for a shop window.

For a permanent address on your own domain, see **`deploy/README-deploy.md`**.

### Configuration

All settings are stored in the database and editable from the admin panel. Environment
variables override the essentials:

| Variable | Default | Purpose |
|---|---|---|
| `BARBER_HOST` | `127.0.0.1` | Bind address |
| `BARBER_PORT` | `8000` | Port |
| `BARBER_DB_PATH` | `data/barber.db` | SQLite database file |
| `BARBER_DATA_DIR` | `data/` | Data directory |
| `BARBER_ADMIN_USER` | `<admin-username>` | Seeded admin username |
| `BARBER_ADMIN_PASS` | `<admin-password>` | Seeded admin password |
| `BARBER_SECURE_COOKIES` | off | Set to `1` behind HTTPS (implied by `--tls`) |

The database is created and seeded with a sample menu, three barbers and opening hours on
first run.

---

## Layout

```
barber-booking/
├── run.py               entry point
├── barber/
│   ├── config.py        paths, ports, environment overrides
│   ├── db.py            schema, migrations, seed data, password hashing
│   ├── timeutil.py      timezone and time parsing helpers
│   ├── settings.py      shop settings accessors
│   ├── availability.py  slot generation and conflict detection
│   ├── booking.py       booking rules, validation, codes
│   ├── auth.py          admin authentication, sessions, CSRF tokens
│   ├── ratelimit.py     sliding-window rate limiter
│   ├── certs.py         self-signed certificate generation
│   ├── api.py           JSON API routes
│   └── server.py        HTTP server, TLS, security headers, static file serving
├── static/
│   ├── index.html       customer booking app
│   ├── admin.html       staff portal
│   ├── css/style.css    design system
│   ├── css/fonts.css    self-hosted @font-face declarations
│   ├── fonts/           Inter + Playfair Display (OFL 1.1)
│   └── js/              app.js (customer), admin.js (staff)
├── deploy/
│   ├── Caddyfile        reverse proxy with automatic Let's Encrypt
│   ├── nginx.conf       reverse proxy for nginx users
│   ├── setup.sh         service account, install, systemd unit
│   ├── start-tunnel.sh  app + public HTTPS tunnel supervisor
│   ├── barber-booking.service
│   ├── barber-booking-tunnel.service
│   └── README-deploy.md domain, DNS and production deployment guide
├── tests/
│   ├── test_core.py     scheduling and booking rules
│   ├── test_api.py      HTTP API integration tests
│   ├── test_security.py security control tests
│   ├── test_tls.py      certificate and TLS tests
│   ├── dom_test.js      front-end tests (jsdom)
│   ├── browser_test.js  end-to-end tests (playwright)
│   └── run_js_tests.sh  runner for the JavaScript suites
├── certs/               generated certificates (git-ignored)
└── data/                SQLite database (created on first run)
```

---

## API

Public endpoints return JSON; admin endpoints require a session cookie.

Every mutating admin call (`POST`, `PUT`, `PATCH`, `DELETE` under `/api/admin/`, except
login) must also send the `X-CSRF-Token` header. The token is returned by
`POST /api/admin/login` and `GET /api/admin/me` as `csrf_token` and is bound to the
session — a request without it is rejected with `403`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Shop details, services and barbers |
| `GET` | `/api/services` | Service menu |
| `GET` | `/api/staff?service_id=` | Barbers, optionally filtered by service |
| `GET` | `/api/availability?service_id=&staff_id=&date=` | Open time slots |
| `GET` | `/api/dates?service_id=&staff_id=` | Days that have availability |
| `POST` | `/api/bookings` | Create a booking |
| `GET` | `/api/bookings/lookup?code=&phone=` | Find a booking |
| `POST` | `/api/bookings/{code}/cancel` | Cancel a booking |
| `POST` | `/api/admin/login` / `logout` | Session handling |
| `GET` | `/api/admin/appointments` | Filterable appointment list |
| `POST` | `/api/admin/appointments` | Book on behalf of a customer |
| `PATCH` | `/api/admin/appointments/{id}` | Change status or reschedule |
| `POST` | `/api/admin/appointments/{id}/cancel` | Cancel a booking |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/admin/services` | Service CRUD |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/admin/staff` | Barber CRUD |
| `GET`/`PUT` | `/api/admin/hours` | Shop and per-barber hours |
| `GET`/`POST`/`DELETE` | `/api/admin/closures` | Holidays and closures |
| `GET`/`PUT` | `/api/admin/settings` | Shop settings |
| `GET` | `/api/admin/stats` | Counts and revenue |

Booking payload:

```json
{
  "service_id": 1,
  "staff_id": 2,
  "date": "2026-11-04",
  "time": "10:30",
  "customer_name": "Jordan Ellis",
  "phone": "555-123-4567",
  "email": "jordan@example.com",
  "notes": "Tight on the sides"
}
```

---

## Tests

```bash
python3 -m unittest discover -s tests    # 100 tests, no dependencies
```

Covers the scheduling rules (slot generation, duration fitting, per-barber hours,
closures, double booking, lead time, booking window, rescheduling, cancellation), the
HTTP layer (auth, validation, CRUD, stats, static serving), the security controls
(CSRF, session cookies, security headers, request limits, path traversal, file
permissions, rate limiting) and TLS (certificate generation, HTTPS serving, the
HTTP→HTTPS redirect).

The front-end suites need Node and are optional:

```bash
npm install jsdom playwright-core
npx playwright install chromium-headless-shell
./tests/run_js_tests.sh
```

`dom_test.js` drives the page in jsdom and walks the whole booking flow.
`browser_test.js` runs the same flow in a real headless Chromium, and also checks that
booked slots vanish from public availability, that a second customer is blocked from
taking the same slot, that cancelling frees it again, and that the admin portal can
create bookings and update shop settings.

---

## Security

The app is hardened for public internet use. What is already handled in code:

| Area | Protection |
|---|---|
| Passwords | `hashlib.scrypt` with a per-user random salt, constant-time comparison |
| Sessions | 256-bit random tokens, `HttpOnly`, `SameSite=Strict`, 12-hour expiry, server-side revocation on logout |
| Brute force | 8 failed logins per username locks that account for 5 minutes; public booking endpoint is capped at 20/hour per IP |
| CSRF | Every mutating `/api/admin/*` call requires an `X-CSRF-Token` header, derived via HMAC from the session token, so it cannot be forged without the `HttpOnly` cookie |
| XSS | Every value interpolated into the DOM goes through `esc()`; no `innerHTML` with raw user data, no inline scripts or event handlers |
| SQL injection | All queries use bound parameters |
| Clickjacking | `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'` |
| Content sniffing | `X-Content-Type-Options: nosniff` |
| CSP | `default-src 'self'` — no inline script, no third-party origins |
| Third-party leakage | Fonts are self-hosted, so the app makes **no external requests** and never leaks visitor IPs to a CDN |
| Request abuse | Request bodies capped at 64 KB (413 beyond that) |
| File exposure | Static serving uses an extension allow-list, so a stray `.env` or `.bak` in `static/` cannot be downloaded |
| Path traversal | Resolved paths are confirmed to stay inside `static/` |
| Information leaks | Generic error responses, stack traces only to the server log, `Server` header does not reveal the Python version |
| Data at rest | The SQLite file is created `0600` (owner read/write only) |

### Before putting it on the internet

1. **Change the default password.** Sign in to `/admin` → *Settings → Password*, or set
   `BARBER_ADMIN_USER` / `BARBER_ADMIN_PASS` before the first launch.
2. **Serve it over HTTPS.** Either `--tls` directly, or a Caddy/nginx proxy with a real
   certificate (see *HTTPS in production* above). Over plain HTTP the session cookie
   can be intercepted on the wire.
3. **Do not expose the port directly.** Keep the app on `127.0.0.1` and let the proxy
   face the internet. The bundled server is a single Python process — fine for a shop's
   traffic, but not a hardened HTTP server.
4. **Back up `data/barber.db`** — it is the only thing holding your bookings, and it
   contains customer names and phone numbers. Copy it while the app is stopped, or use
   `sqlite3 data/barber.db ".backup backup.db"`.
5. **Review the seeded content** in *Settings* (shop name, address, phone, timezone,
   opening hours) — the defaults are placeholders.

### Known limits

- One shared staff password — there are no per-user accounts or roles, so every staff
  member sees everything. Add a users table with roles if you need separation of duties.
- No email or SMS confirmation; the booking code shown on screen is the only proof.
- No audit log of staff actions.
- The bundled server is a single Python process. It is fine for a shop's traffic, but it
  is not a hardened HTTP server — a reverse proxy is the right front door.

---

## Notes

- Passwords are hashed with `hashlib.scrypt`; sessions use random tokens in `HttpOnly`
  cookies and expire after 12 hours.
- Timestamps are stored in UTC and converted to the shop's timezone for display, so the
  app behaves correctly across daylight-saving changes.
- The whole app is a single SQLite file — easy to back up, copy or deploy.
