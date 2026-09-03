# Linux VPS (`linux/`)

This folder is **only** for a separate Linux VPS that runs Docker. It does not replace the Windows home PC.

| Machine | What to use | Schedule |
| --- | --- | --- |
| **Windows home** | `scheduler\Register-TradingDailyTask.ps1`, `python -m webapp` | 08:00 local (unchanged) |
| **Linux VPS** | this `linux/` folder + Docker Compose + systemd | weekdays 09:00 IST (NSE pre-open) |

Do not run `docker compose` on the Windows box if you want that machine to keep working as it does today. Clone the **same** GitHub repo on the VPS and use `linux/` there.

## Why not a second GitHub repository?

A separate GitHub project would copy `daily_once_runner.py`, `scheduler/jobs.json`, the scanners, and `webapp/`. Those files would drift from Windows. Keep one repo:

- Windows ignores `linux/`
- The VPS clones this repo and deploys only `linux/`

No Terraform. One Ubuntu VM is enough.

## VPS size and cost (Sept 2026 list prices — confirm before buying)

Need about **2 vCPU, 4 GB RAM, 40–80 GB SSD**, Ubuntu 24.04. 1 GB is too tight.

| Option | Approx. / month | Notes |
| --- | --- | --- |
| Hetzner CPX22 (EU) | about €8 (~$9–10) | Cheapest reliable pick |
| DigitalOcean Basic 4 GB | $24 | Bangalore region if you want the VM in India |
| AWS Lightsail 4 GB | $24 | Mumbai region available |
| Oracle Always Free ARM | $0 | Capacity often exhausted |

## VPS steps

```bash
sudo git clone -b cursor/linux-vps-aec9 https://github.com/karagis75/trading.git /opt/trading
sudo chown -R "$USER:$USER" /opt/trading
cd /opt/trading
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
cp linux/.env.example linux/.env
nano linux/.env   # strong POSTGRES_PASSWORD
sudo timedatectl set-timezone Asia/Kolkata
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw --force enable
sudo ./linux/install-vps.sh --home /opt/trading --set-timezone
```

Open `http://<vps-ip>:8000/`.

Compose (manual):

```bash
cd /opt/trading/linux
docker compose up -d --build
docker compose run --rm daily daily_once_runner.py --status
```

Secrets stay in `linux/.env`. Postgres is not published. The once-per-day gate is still `daily_once_runner.py`.

Optional live pass (Yahoo/NSE, long): `sudo systemctl start trading-daily.service`

To use 09:15 continuous open, edit `linux/trading-daily.timer` then re-run `install-vps.sh`.

## Uninstall on the VPS (does not touch Windows)

```bash
sudo systemctl disable --now trading-daily.timer trading-web.service
sudo rm -f /etc/systemd/system/trading-web.service \
           /etc/systemd/system/trading-daily.service \
           /etc/systemd/system/trading-daily.timer
sudo systemctl daemon-reload
cd /opt/trading/linux && docker compose down
```
