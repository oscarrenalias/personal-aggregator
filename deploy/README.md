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

## Quick install (one-liner)

On the Pi, run:

```bash
curl -fsSL https://raw.githubusercontent.com/oscarrenalias/personal-aggregator/main/deploy/bootstrap.sh | sh
```

This downloads the latest release assets, asks for your LLM API key and whether to expose
the UI on your LAN, installs the stack, and starts it — then prints the URL to open. It
needs `docker` (with the Compose plugin) and `sudo` already present.

Non-interactive (e.g. unattended), pass answers as env vars:

```bash
curl -fsSL https://raw.githubusercontent.com/oscarrenalias/personal-aggregator/main/deploy/bootstrap.sh \
  | NONINTERACTIVE=1 OPENAI_API_KEY=sk-... WEB_BIND=0.0.0.0 sh
```

> Prefer not to pipe a script into a shell sight-unseen? Read it first:
> `curl -fsSL .../deploy/bootstrap.sh` — or follow the manual steps below.

## Manual install

1. **Download the release assets** from the [latest GitHub Release](../../releases/latest).
   You need: `install.sh`, `docker-compose.prod.yml`, `aggregator.service`, and `env.example`.
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

By default the `web` service is published on **`0.0.0.0:8000`**, so the UI is reachable from any device on your home LAN at `http://<pi-ip>:8000/` (e.g. `http://192.168.1.50:8000/`). Find the Pi's address with `hostname -I`.

This is controlled by **`WEB_BIND`** in `/opt/personal-aggregator/.env`:

| `WEB_BIND` | Exposure |
|---|---|
| `0.0.0.0` (default) | Any device on the LAN (and Tailscale) |
| `127.0.0.1` | Pi-local only — use this if you front the UI exclusively with Tailscale Serve |

Change it and `sudo ./install.sh update` (or `docker compose -f docker-compose.prod.yml up -d`) to apply. There is no app-level authentication, so on `0.0.0.0` anyone on your LAN can reach it — keep the Pi on a trusted network. (The in-container bind, `WEB_HOST`, is fixed to `0.0.0.0` by the compose file and is unrelated to this.)

### Tailscale Serve (optional — HTTPS over your tailnet, reachable away from home)

If you also want secure access from outside your home network, set `WEB_BIND=127.0.0.1` and put **Tailscale Serve** in front:

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

## Managing feeds, interests & categories (`aggregator` CLI)

The installer places a self-contained **`aggregator`** script at **`/opt/personal-aggregator/aggregator`**. It runs the admin CLI in a one-off container against the running stack, so you can operate the app without any source checkout. Docker needs root, so use `sudo` (or add your user to the `docker` group).

```bash
sudo /opt/personal-aggregator/aggregator --help
sudo /opt/personal-aggregator/aggregator sources add -n "BBC News" -u "http://feeds.bbci.co.uk/news/rss.xml"
sudo /opt/personal-aggregator/aggregator sources list
sudo /opt/personal-aggregator/aggregator sources import-opml feedly.opml   # import from Feedly
sudo /opt/personal-aggregator/aggregator sources export-opml backup.opml   # export them
sudo /opt/personal-aggregator/aggregator profile set "Software architecture, AI, gaming reviews…"
sudo /opt/personal-aggregator/aggregator categories list                   # the LLM classification set
sudo /opt/personal-aggregator/aggregator categories set-description "Software Engineering" "…; EXCLUDES games"
sudo /opt/personal-aggregator/aggregator articles rerank --all             # re-score existing articles
```

> Prefer a short command? Add an alias to your shell: `alias aggregator='sudo /opt/personal-aggregator/aggregator'`.
>
> The directory you run it from is mounted into the container, so file arguments (OPML import/export) work with normal relative paths — `cd` to where your `.opml` file is first.
>
> **Categories** are seeded with sensible defaults on first install (Technology & IT, Cloud & Architecture, Software Engineering, AI, Gaming) — these are ordinary editable rows, not hardcoded. Rename, re-describe, disable, add, or remove them freely with `aggregator categories …`.

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

There are two ways to upgrade, depending on whether you also want the latest deploy
files (compose, the `aggregator` wrapper) or just newer container images.

### Full upgrade (recommended) — re-run the bootstrap

```bash
curl -fsSL https://raw.githubusercontent.com/oscarrenalias/personal-aggregator/main/deploy/bootstrap.sh | NONINTERACTIVE=1 sh
```

It's idempotent: refreshes `docker-compose.prod.yml`, `install.sh`, and the `aggregator`
script from the latest release, **pulls the latest images**, and restarts — while
**preserving your `.env`** and the Postgres volume. Use this when a release changes deploy
files (new services/ports, or a new helper like the `aggregator` CLI). `NONINTERACTIVE=1`
skips the key/LAN prompts since you've already configured `.env`.

### Lightweight — images only

If you only want newer images and your deploy files are already current:

```bash
cd /opt/personal-aggregator
sudo docker compose -f docker-compose.prod.yml pull
sudo docker compose -f docker-compose.prod.yml up -d
```

(`sudo ./install.sh update` does exactly this.) This pulls the latest `:latest` images and
restarts, but does **not** refresh the compose file or the `aggregator` wrapper — so it
won't pick up deploy-file changes. The release notes call out when a release changes those.

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
