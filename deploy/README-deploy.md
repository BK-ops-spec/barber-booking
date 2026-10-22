# Putting this on a real domain

Two ways to get a permanent address. Pick **one**.

- **Option A (recommended)** — Cloudflare Registrar + a named tunnel. No router
  changes, no public IP exposure, automatic certificates, free at every tier.
- **Option B** — Caddy + port forwarding. More moving parts, exposes your home
  connection, breaks if your ISP changes your IP.

---

## Option A — Cloudflare (recommended)

### 1. Buy the domain

[Cloudflare Registrar](https://www.cloudflare.com/products/registrar/) sells domains at
cost with no markup. Pick a `.com` (or whatever fits the shop). Buying at Cloudflare
means the nameservers are set for you automatically — no separate DNS host to configure.

Any registrar works. If you buy elsewhere, note the two nameservers Cloudflare gives you
and set them at the registrar.

### 2. Create the tunnel

On this machine:

```bash
cloudflared tunnel login          # opens a browser, pick your domain
cloudflared tunnel create barber-booking
```

That writes a credentials file to `~/.cloudflared/<tunnel-id>.json`. Create
`~/.cloudflared/config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: booking.yourdomain.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Point DNS at the tunnel (this writes the CNAME for you):

```bash
cloudflared tunnel route dns barber-booking booking.yourdomain.com
```

### 3. Run it as a service

Replace the quick-tunnel script with a named-tunnel one:

```bash
cloudflared tunnel run barber-booking
```

Use the same systemd user unit as the quick tunnel, changing `ExecStart` to:

```ini
ExecStart=%h/.local/bin/cloudflared tunnel --no-autoupdate run barber-booking
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user restart barber-booking-tunnel
```

Cloudflare provisions the certificate on the first request and renews it
automatically. It takes a minute or two for the first one to appear.

### 4. Lock it down

In the Cloudflare dashboard:

- **SSL/TLS → Full (strict)**, not "Flexible". Flexible mode does not encrypt the
  hop between Cloudflare and this machine, and Cloudflare itself warns against it.
- **Security → WAF → Managed rules** on, to block obvious bots.
- Add a **Cloudflare Access** policy if you want the `/admin` path restricted to
  your own IP. The app's own password is still required, so this is a second layer.

---

## Option B — Caddy with port forwarding

Needs a domain with an A record pointing at your public IP, ports 80 and 443
forwarded on the router, and a static public IP.

### 1. Domain

Point an `A` record for `booking.yourdomain.com` at your public IP, and set the
nameservers to Cloudflare (or any DNS host that supports the ACME HTTP-01 challenge).

### 2. Router

On your router at `<router-ip>`:

- **Port forwarding:** external 80 → `<server-ip>:80`, external 443 → `<server-ip>:443`
- **Disable remote administration** (WAN / remote management / "allow access from the
  internet"). Right now the router's own login page is reachable at
  `http://<your-public-ip>/` from the internet. Turn that off.
- **Change the default admin password.**
- **Disable UPnP** on the router — it lets any device on your LAN open ports at will.
- **Check your public IP is stable.** If your ISP gives you a dynamic address, either
  request a static one or use Option A instead.

### 3. Caddy

```bash
sudo apt install caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the domain
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy gets a Let's Encrypt certificate over HTTP-01 and renews it on its own.

### 4. App

```bash
systemctl --user stop barber-booking-tunnel
# then run the plain app on localhost and let Caddy front it
```

---

## The app itself

Whichever option you pick, the app stays on `127.0.0.1:8000` and only the proxy or
tunnel talks to the internet.

Before going live:

1. **Change the admin password** — `/admin` → *Settings → Password*. The seed default
   is `<admin-username>` / `<admin-password>`.
2. **Set the real shop details** in *Settings*: name, address, phone, timezone, hours.
3. **Back up `data/barber.db`** on a schedule. It holds every booking.

## Keeping the URL alive

The quick tunnel URL is **random and changes every time the tunnel restarts**. That is
fine for testing but not for printing on a shop window. Use Option A for a stable
address.

Current URL, whatever it is right now:

```bash
cat ~/.local/state/barber-booking/public-url
```

## Commands

```bash
systemctl --user status barber-booking-tunnel    # is it up
systemctl --user restart barber-booking-tunnel   # new URL
systemctl --user stop barber-booking-tunnel      # take it offline
tail -f ~/.local/state/barber-booking/logs/cloudflared.log
```
