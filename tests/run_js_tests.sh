#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8123}"
BASE="http://127.0.0.1:${PORT}"
DATA_DIR="$(mktemp -d)"

if ! command -v node >/dev/null 2>&1; then
  echo "node not found - skipping JavaScript tests"
  exit 0
fi

cd "$ROOT"
BARBER_DB_PATH="${DATA_DIR}/test.db" python3 run.py 127.0.0.1 "${PORT}" >"${DATA_DIR}/server.log" 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "${SERVER_PID}" 2>/dev/null || true
  rm -rf "${DATA_DIR}"
}
trap cleanup EXIT

for _ in $(seq 1 40); do
  if curl -sf "${BASE}/api/config" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

status=0

if [ -d node_modules/jsdom ]; then
  BASE="${BASE}" node tests/dom_test.js || status=1
else
  echo "jsdom not installed - run 'npm install jsdom' to enable DOM tests"
fi

if [ -d node_modules/playwright-core ]; then
  BASE="${BASE}" node tests/browser_test.js || status=1
else
  echo "playwright-core not installed - run 'npm install playwright-core' to enable browser tests"
fi

# The same suites can be pointed at a TLS server:
#   BASE=https://127.0.0.1:8443 ./tests/run_js_tests.sh
# The self-signed certificate is trusted automatically by the harnesses.

exit "${status}"
