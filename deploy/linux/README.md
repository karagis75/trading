# Linux VPS

Replace Windows Task Scheduler with a Linux VPS: Compose keeps the dashboard up, and systemd fires the scanners on **weekdays at 09:00 Asia/Kolkata** (NSE pre-open; continuous trading starts 09:15).

The once-per-calendar-day gate is still `daily_once_runner.py` (lock + `scheduler/state/run-state.json`). systemd only starts Compose on boot and invokes `docker compose run --rm daily` on the timer. Weekends are skipped. NSE holidays still fire unless you stop the timer that day.

## Do you need Terraform?

**No.** This is one Ubuntu VM, Docker Compose, and two systemd units. Terraform (or Ansible, Kubernetes, ECS) adds cost and moving parts without helping a single-box scanner. Use the console or CLI to create the VPS, then the steps below. Revisit Terraform only if you later want several identical environments as code.

## VPS size and cost

Need about **2 vCPU, 4 GB RAM, 40–80 GB SSD**, Ubuntu 24.04, and outbound HTTPS (Yahoo + NSE). 1 GB RAM is too tight for Postgres + pandas + a Nifty 500 pass.

List prices as of September 2026 (check the provider page before you buy; tax and bandwidth extra):

| Option | Spec (typical) | Approx. / month | Notes |
| --- | --- | --- | --- |
| **Hetzner Cloud CPX22** (EU) | 2 vCPU, 4 GB, 80 GB | about €8 (~$9–10) | Best price. Yahoo/NSE still work from EU. |
| **DigitalOcean Basic 4 GB** | 2 vCPU, 4 GB, 80 GB | $24 | Has a **Bangalore** region if you want the VM in India. |
| **AWS Lightsail 4 GB** | 2 vCPU, 4 GB, 80 GB | $24 | Simple AWS billing; Mumbai region available. |
| **Oracle Cloud Always Free ARM** | up to 2 OCPU, 12 GB | $0 | Capacity is often exhausted; fine to try, not a guarantee. |

**Pick:** Hetzner CPX22 if cheapest is the goal. DigitalOcean Bangalore 4 GB if you want the box in India. Skip 512 MB–1 GB plans.

You do not need a managed Postgres, load balancer, or extra block volume for this stack.

## Steps on the VPS

### 1. Create the VM

Ubuntu 24.04, 4 GB RAM, add your SSH key, open **22** (SSH) and **8000** (dashboard) only. Set hostname if you like. SSH in as a sudo user.

### 2. Clone this branch

```bash
sudo mkdir -p /opt/trading
sudo git clone -b cursor/linux-vps-aec9 https://github.com/karagis75/trading.git /opt/trading
sudo chown -R "$USER:$USER" /opt/trading
cd /opt/trading
```

Already cloned:

```bash
cd /opt/trading
git fetch origin
git checkout cursor/linux-vps-aec9
```

### 3. Install Docker Engine + Compose plugin

<https://docs.docker.com/engine/install/>

On Ubuntu:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker compose version
```

### 4. Secrets, timezone, firewall

```bash
cd /opt/trading
cp .env.example .env
nano .env   # set a strong POSTGRES_PASSWORD
sudo timedatectl set-timezone Asia/Kolkata
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw --force enable
```

Do not commit `.env`. Postgres is not published; only port 8000 is.

### 5. Install systemd (replaces Windows 08:00)

```bash
cd /opt/trading
sudo ./deploy/linux/install-vps.sh --home /opt/trading --set-timezone
```

That writes units, enables the dashboard on boot, and enables **Mon–Fri 09:00 IST**.

Open `http://<vps-ip>:8000/`. Put nginx/Caddy in front later if you want HTTPS; not required to run.

### 6. Confirm the timer (no live scan required)

```bash
timedatectl
systemctl status trading-web.service trading-daily.timer
systemctl list-timers trading-daily.timer
docker compose run --rm daily daily_once_runner.py --status
```

`OnCalendar` should show weekdays 09:00. `already_succeeded` is false until a real pass finishes.

Optional one pass now (this **does** hit Yahoo/NSE and can take a long time):

```bash
sudo systemctl start trading-daily.service
```

## Daily pass after install

Automatic: `trading-daily.timer` weekdays 09:00 Asia/Kolkata.

Manual (still once-per-day gated):

```bash
sudo systemctl start trading-daily.service
# or
cd /opt/trading && docker compose run --rm daily
docker compose run --rm daily daily_once_runner.py --status
```

A successful run writes `scheduler/state/run-state.json`. Later triggers the same calendar day exit immediately. Failed jobs retry on the next timer. `--force` reruns everything.

Enabled jobs are those in `scheduler/jobs.json`. Nimblr and the one-time validate job stay disabled.

To use continuous-market open instead of pre-open, edit `deploy/linux/trading-daily.timer` to `OnCalendar=Mon-Fri 09:15:00`, then `sudo ./deploy/linux/install-vps.sh --home /opt/trading`.

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
