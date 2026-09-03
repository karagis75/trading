# Linux VPS

Run the Compose dashboard on a Linux VPS and fire the daily scanners at 08:00 Asia/Kolkata without Windows Task Scheduler.

The once-per-calendar-day gate is still `daily_once_runner.py` (lock + `scheduler/state/run-state.json`). systemd only starts Compose on boot and invokes `docker compose run --rm daily` on a timer.

## 1. Clone this branch

```bash
sudo mkdir -p /opt/trading
sudo git clone -b cursor/linux-vps-aec9 https://github.com/karagis75/trading.git /opt/trading
cd /opt/trading
```

If you already cloned another branch:

```bash
cd /opt/trading
git fetch origin
git checkout cursor/linux-vps-aec9
```

## 2. Install Docker Engine + Compose plugin

Follow the current Docker Engine install for your distro:
<https://docs.docker.com/engine/install/>

On Ubuntu you can also use:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Confirm `docker compose version` works, then log out and back in (or `newgrp docker`).

## 3. Secrets and timezone

```bash
cd /opt/trading
cp .env.example .env
nano .env   # set a strong POSTGRES_PASSWORD
sudo timedatectl set-timezone Asia/Kolkata
```

Do not commit `.env`. Postgres is not published to the host; only the dashboard port `8000` is.

## 4. Install systemd units

```bash
cd /opt/trading
sudo ./deploy/linux/install-vps.sh --home /opt/trading --set-timezone
```

That:

- writes `.env` from `.env.example` if missing
- installs `trading-web.service`, `trading-daily.service`, and `trading-daily.timer`
- enables the dashboard on boot (`docker compose up -d`)
- enables the 08:00 timer

Open `http://<vps-ip>:8000/`. Restrict port 8000 with `ufw` (or a reverse proxy) if the VPS is public.

## 5. Daily pass

Automatic: `trading-daily.timer` at 08:00 Asia/Kolkata.

Manual (does not skip the once-per-day gate):

```bash
sudo systemctl start trading-daily.service
# or
cd /opt/trading && docker compose run --rm daily
docker compose run --rm daily daily_once_runner.py --status
```

A successful run writes `scheduler/state/run-state.json`. Later triggers the same calendar day exit immediately. Failed jobs retry on the next timer fire. `--force` reruns everything.

Enabled jobs are those in `scheduler/jobs.json`. Nimblr and the one-time validate job stay disabled.

## Uninstall

```bash
sudo systemctl disable --now trading-daily.timer trading-web.service
sudo rm -f /etc/systemd/system/trading-web.service \
           /etc/systemd/system/trading-daily.service \
           /etc/systemd/system/trading-daily.timer
sudo systemctl daemon-reload
cd /opt/trading && docker compose down
```

Postgres data remains in the Compose volume until you `docker compose down -v`.
