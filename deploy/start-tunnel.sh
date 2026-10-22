#!/usr/bin/env bash
#
# Supervises the booking app and the public HTTPS tunnel.
#
# The Cloudflare quick tunnel URL is regenerated whenever cloudflared restarts,
# so the current URL is always written to a file you can read:
#
#   cat ~/.local/state/barber-booking/public-url
#
# For a permanent address on your own domain, use a named tunnel instead
# (see deploy/README-deploy.md).

set -uo pipefail

APP_DIR=${APP_DIR:-/home/bsris/barber-booking}
PORT=${PORT:-8000}
STATE_DIR=${STATE_DIR:-$HOME/.local/state/barber-booking}
URL_FILE="$STATE_DIR/public-url"
LOG_DIR="$STATE_DIR/logs"
TUNNEL_LOG="$LOG_DIR/cloudflared.log"
BIN=${CLOUDFLARED_BIN:-$HOME/.local/bin/cloudflared}

mkdir -p "$STATE_DIR" "$LOG_DIR"

if [ ! -x "$BIN" ]; then
    echo "cloudflared not found at $BIN" >&2
    exit 1
fi

# --- start the app ---------------------------------------------------------
if ! curl -sf "http://127.0.0.1:${PORT}/api/config" >/dev/null 2>&1; then
    echo "starting app on 127.0.0.1:${PORT}"
    ( cd "$APP_DIR" && exec python3 run.py 127.0.0.1 "${PORT}" ) >>"$LOG_DIR/app.log" 2>&1 &
    echo $! > "$STATE_DIR/app.pid"
    sleep 2
fi

# --- keep the tunnel alive -------------------------------------------------
# cloudflared prints its URL on stderr; scrape it out and keep the process
# supervised so a crash does not take the public URL offline.
while true; do
    rm -f "$URL_FILE"
    # Only read log output produced by this run, otherwise a previous run's
    # URL can be picked up and published after a restart.
    LOG_OFFSET=$(stat -c %s "$TUNNEL_LOG" 2>/dev/null || echo 0)
    "$BIN" tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate \
        >>"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    echo "$TUNNEL_PID" > "$STATE_DIR/tunnel.pid"

    for _ in $(seq 1 60); do
        url=$(tail -c "+$((LOG_OFFSET + 1))" "$TUNNEL_LOG" 2>/dev/null \
              | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1)
        if [ -n "$url" ]; then
            echo "$url" > "$URL_FILE"
            echo "public URL: $url"
            break
        fi
        if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    if kill -0 "$TUNNEL_PID" 2>/dev/null; then
        wait "$TUNNEL_PID"
    fi
    echo "tunnel exited, restarting in 5s" >>"$TUNNEL_LOG"
    sleep 5
done
