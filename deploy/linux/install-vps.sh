#!/usr/bin/env bash
# Install systemd units so a Linux VPS boots the Compose dashboard and
# fires daily_once_runner.py weekdays at 09:00 Asia/Kolkata (NSE pre-open).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-vps.sh [options]

Options:
  --home DIR       Repository root (default: parent of deploy/linux)
  --unit-dir DIR   Where to write unit files (default: /etc/systemd/system)
  --no-enable      Write units only; do not systemctl enable/start
  --set-timezone   timedatectl set-timezone Asia/Kolkata (requires root)
  -h, --help       Show this help

This script does not run a live Nifty 500 scan. After install:
  sudo systemctl start trading-web.service
  sudo systemctl start trading-daily.service   # optional one pass now
EOF
}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TRADING_HOME=$(cd "${SCRIPT_DIR}/../.." && pwd)
UNIT_DIR=/etc/systemd/system
ENABLE=1
SET_TZ=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)
      TRADING_HOME=$(cd "$2" && pwd)
      shift 2
      ;;
    --unit-dir)
      UNIT_DIR=$2
      shift 2
      ;;
    --no-enable)
      ENABLE=0
      shift
      ;;
    --set-timezone)
      SET_TZ=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${TRADING_HOME}/docker-compose.yml" ]]; then
  echo "docker-compose.yml not found in ${TRADING_HOME}" >&2
  exit 1
fi
if [[ ! -f "${TRADING_HOME}/daily_once_runner.py" ]]; then
  echo "daily_once_runner.py not found in ${TRADING_HOME}" >&2
  exit 1
fi

if [[ ! -f "${TRADING_HOME}/.env" ]]; then
  if [[ -f "${TRADING_HOME}/.env.example" ]]; then
    cp "${TRADING_HOME}/.env.example" "${TRADING_HOME}/.env"
    echo "Wrote ${TRADING_HOME}/.env from .env.example — change POSTGRES_PASSWORD before real use."
  else
    echo "Missing ${TRADING_HOME}/.env (and no .env.example to copy)." >&2
    exit 1
  fi
fi

mkdir -p "${UNIT_DIR}"
for unit in trading-web.service trading-daily.service trading-daily.timer; do
  sed "s|__TRADING_HOME__|${TRADING_HOME}|g" "${SCRIPT_DIR}/${unit}" > "${UNIT_DIR}/${unit}"
  echo "Installed ${UNIT_DIR}/${unit}"
done

if [[ "${SET_TZ}" -eq 1 ]]; then
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone Asia/Kolkata
  else
    echo "timedatectl not found; set the host timezone to Asia/Kolkata manually." >&2
    exit 1
  fi
fi

if [[ "${ENABLE}" -eq 1 ]]; then
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; units were written to ${UNIT_DIR} but not enabled." >&2
    exit 0
  fi
  if [[ ! -d /run/systemd/system ]]; then
    echo "systemd is not PID 1; units were written to ${UNIT_DIR} but not enabled."
    exit 0
  fi
  systemctl daemon-reload
  systemctl enable --now trading-web.service
  systemctl enable --now trading-daily.timer
  echo "Enabled trading-web.service and trading-daily.timer."
  echo "Dashboard: docker compose binds 0.0.0.0:8000. Open http://<vps-ip>:8000/"
  echo "The weekday 09:00 IST timer does not fire a scan until the next calendar slot (Persistent=true catches missed boots)."
fi
