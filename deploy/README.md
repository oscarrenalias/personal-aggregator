# Personal Aggregator — Raspberry Pi Deployment

Runs the headless aggregator stack (postgres → migrate → retriever → processor →
summarize-rank) as a systemd-managed Docker Compose service.

---

## Prerequisites

- **Raspberry Pi OS (64-bit, Bookworm or later)** — the published images target `linux/arm64`.
- **Docker Engine** — install via the [official script](https://docs.docker.com/engine/install/raspberry-pi-os/):
  ```
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER   # log out and back in afterwards
  ```
- **`sudo` access** — the installer creates `/opt/personal-aggregator` and installs a
  systemd unit under `/etc/systemd/system/`.
- **Tailscale** (optional) — recommended if you want to reach the web UI from outside
  the local network. Install separately; no aggregator configuration is needed.

---

## One-time install

1. **Download the release assets** from the [latest GitHub Release](../../releases/latest).
   You need: `install.sh`, `docker-compose.prod.yml`, `aggregator.service`, and `.env.example`.
   Place all four files in the same directory (e.g. `~/aggregator-install/`).

2. **Make the script executable and run it:**
   ```bash
   chmod +x install.sh
   sudo ./install.sh install
   ```
   The installer:
   - Creates `/opt/personal-aggregator/`
   - Copies `docker-compose.prod.yml` into that directory
   - Creates `/opt/personal-aggregator/.env` from `.env.example` (only on first run;
     an existing `.env` is never overwritten)
   - Sets `IMAGE_PREFIX` and `APP_VERSION` in `.env`
   - Installs `aggregator.service` to `/etc/systemd/system/`
   - Runs `systemctl enable aggregator` and `systemctl start aggregator`

   To preview what will happen without making changes, use `--check`:
   ```bash
   sudo ./install.sh --check install
   ```

3. **Fill in the required environment variables** in `/opt/personal-aggregator/.env`:
   ```bash
   sudo nano /opt/personal-aggregator/.env
   ```
   At minimum you must set your LLM API key. The default model is `gpt-4.1-mini`:
   ```
   OPENAI_API_KEY=sk-...
   ```
   If you switch to a Claude model (`LLM_MODEL=claude-...`), set `ANTHROPIC_API_KEY`
   instead. You must also set the production `DATABASE_URL` (pointing at the `postgres`
   compose service, not `localhost`):
   ```
   DATABASE_URL=postgresql://aggregator:aggregator@postgres:5432/aggregator
   ```

4. **Restart the service** to pick up the populated `.env`:
   ```bash
   sudo systemctl restart aggregator
   ```

---

## Enable / start the service

The installer enables and starts the service automatically. If you ever need to manage
it manually:

```bash
sudo systemctl enable aggregator   # start on boot
sudo systemctl start aggregator    # start now
sudo systemctl stop aggregator     # stop the stack
sudo systemctl status aggregator   # check whether it is running
```

---

## Accessing the web UI

The `web` service (aggregator-web) binds to `127.0.0.1` on port **8000** by default. This means it is only reachable from the Pi itself — it is **never exposed on `0.0.0.0`** and is not directly accessible from other machines.

### Tailscale Serve (recommended — HTTPS over your tailnet)

Use **Tailscale Serve** to expose the web UI to all your Tailscale-connected devices with automatic HTTPS:

```bash
sudo tailscale serve --bg 8000
```

This proxies `https://<hostname>.your-tailnet.ts.net/` to the local port 8000. Your devices (phone, laptop, etc.) can reach the UI at that URL without any open firewall ports.

To check the current serve configuration:
```bash
tailscale serve status
```

To remove it later:
```bash
sudo tailscale serve --remove 8000
```

### iOS "Add to Home Screen" (PWA)

The web UI ships as a Progressive Web App. To install it on iOS:

1. Open the Tailscale Serve URL in Safari.
2. Tap the **Share** button → **Add to Home Screen**.
3. The app opens full-screen in standalone mode, without the Safari browser chrome.

The service worker caches the app shell and static assets so the UI loads instantly on subsequent visits.

---

## Viewing logs

**Container logs** (per-service, the most useful view):
```bash
cd /opt/personal-aggregator
docker compose -f docker-compose.prod.yml logs -f
```

Follow a single service:
```bash
docker compose -f docker-compose.prod.yml logs -f summarize-rank
```

**systemd journal** (service lifecycle events — starts, stops, failures):
```bash
journalctl -u aggregator -f
```

---

## Updating

Pull the latest images and restart the running stack:

```bash
sudo ./install.sh update
```

This is equivalent to:
```bash
cd /opt/personal-aggregator
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Dry-run preview:
```bash
sudo ./install.sh --check update
```

> **Note:** `install.sh update` only pulls new images and restarts the stack using the
> compose file already in `/opt/personal-aggregator/`. It does **not** replace the compose
> file. To pick up a new `docker-compose.prod.yml` from a release, download the release
> assets and re-run `install.sh install` — the `.env` will be preserved.

---

## Rolling back to a previous version

1. Open `/opt/personal-aggregator/.env` and pin `APP_VERSION` to the tag you want:
   ```bash
   sudo nano /opt/personal-aggregator/.env
   ```
   ```
   APP_VERSION=v0.1.3
   ```

2. Run update to pull and restart at the pinned version:
   ```bash
   sudo ./install.sh update
   ```

To return to tracking the latest release, set `APP_VERSION=latest` and run
`install.sh update` again.

---

## Postgres data backup

Postgres data is stored in the **`pgdata` named Docker volume**. Back it up with:

```bash
docker run --rm \
  -v personal-aggregator_pgdata:/data \
  -v /tmp:/backup \
  busybox tar czf /backup/pgdata-$(date +%Y%m%d).tar.gz -C /data .
```

Restore by reversing the operation into a fresh `pgdata` volume before starting the
stack. The volume is preserved across `install.sh update` runs and across container
restarts; it is only removed if you explicitly run `docker compose down -v`.

---

## Auto-update timer (optional, default off)

By default, updates are manual (`install.sh update`). You can add a systemd timer to
automate nightly pulls if you prefer hands-off continuous deployment:

```ini
# /etc/systemd/system/aggregator-update.timer
[Unit]
Description=Nightly personal-aggregator update

[Timer]
OnCalendar=03:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/aggregator-update.service
[Unit]
Description=Pull and restart personal-aggregator

[Service]
Type=oneshot
WorkingDirectory=/opt/personal-aggregator
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml pull
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
```

Enable with:
```bash
sudo systemctl enable --now aggregator-update.timer
```

This is not installed by `install.sh` and is entirely optional.
