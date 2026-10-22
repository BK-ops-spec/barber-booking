#!/usr/bin/env bash
#
# One-time setup for a production install behind a reverse proxy.
#
#   sudo ./deploy/setup.sh
#
# Creates a service account, installs the app under /opt, points the data
# directory at /var/lib, and enables the systemd unit. It does NOT start the
# service — configure your TLS proxy first, then run:
#
#   sudo systemctl start barber-booking

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/barber-booking}
DATA_DIR=${DATA_DIR:-/var/lib/barber-booking}
SERVICE_USER=${SERVICE_USER:-barber}
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "error: run this with sudo" >&2
    exit 1
fi

echo "==> creating service account"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> installing application to $APP_DIR"
mkdir -p "$APP_DIR"
for item in barber static run.py README.md; do
    cp -r "$SOURCE_DIR/$item" "$APP_DIR/"
done
find "$APP_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> preparing data directory"
mkdir -p "$DATA_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 700 "$DATA_DIR"

echo "==> installing systemd unit"
install -m 644 "$SOURCE_DIR/deploy/barber-booking.service" /etc/systemd/system/barber-booking.service
systemctl daemon-reload
systemctl enable barber-booking

echo "==> done"
echo
echo "Next steps:"
echo "  1. Set your admin password before first start:"
echo "       BARBER_ADMIN_USER=youruser BARBER_ADMIN_PASS='a-strong-password' \\"
echo "       systemctl start barber-booking"
echo "  2. Put a TLS proxy in front (see deploy/Caddyfile or deploy/nginx.conf)."
echo "  3. Change the database directory in the unit file if you moved it."
