# Personal Aggregator — Raspberry Pi Deployment

Runs the headless aggregator stack (postgres → migrate → retriever → processor →
summarize-rank → clusterer) as a systemd-managed Docker Compose service.
The **clusterer** groups ranked articles into threads; it depends on summarize-rank
having completed scoring before articles are clustered. No separate operational
actions are needed — it starts automatically with the rest of the stack.

After each normal clustering cycle the clusterer runs a **consolidation pass** that
performs three sub-steps in order: (1) a **merge pass** — near-duplicate active threads
whose composite entity/topic/FTS similarity meets `CLUSTERER_MERGE_SIMILARITY_FLOOR` are
confirmed by the LLM and absorbed into a single thread (up to `CLUSTERER_MAX_MERGE_CHECKS`
LLM calls per cycle); (2) a **surfacing pass** — every active thread is re-scored with a
deterministic grade (0–100) and marked `surfaced=true` when it meets
`CLUSTERER_SURFACE_MIN_GRADE`, `CLUSTERER_SURFACE_MIN_SOURCES`, and
`CLUSTERER_SURFACE_MIN_MEMBERS`; (3) a **retention prune** — threads whose
`last_updated` is older than `CLUSTERER_THREAD_RETENTION_DAYS` days are permanently
deleted (article rows are not affected). The pass is idempotent: re-running it on an
already-curated set produces no further changes.

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

## Accessing the MCP server

The `mcp` service exposes a **Model Context Protocol (MCP) endpoint** (Streamable HTTP transport) so external agents can query and act on the aggregator.

**Article tools:**
- `search_articles` — full-text search with optional filters (since, category, source_id, limit).
- `list_articles` — list articles by view (unread/saved/important/etc.) with optional filters.
- `get_article` — fetch a single article by id.
- `mark_read` / `mark_unread` — toggle read state.
- `save_article` / `unsave_article` — toggle saved state.

**Interest profile:**
- `get_interest_profile` — return the current free-text interest profile.
- `set_interest_profile` — replace the profile; takes effect on the next summarize-rank cycle.

**Source management:**
- `list_sources` — list all configured sources.
- `add_source` — add a new RSS/Atom source by name and feed URL.
- `enable_source` / `disable_source` — toggle whether the retriever polls a source.
- `set_source_interval` — update the polling interval (seconds) for a source.
- `refresh_source_now` — force a source to be polled on the next retriever cycle.
- `remove_source` — **DESTRUCTIVE**: permanently deletes the source and cascade-deletes every article belonging to it. Irreversible.

**Category management:**
- `list_categories` — list all categories.
- `add_category` — create a new category with optional description, sort order, and enabled flag.
- `rename_category` — rename an existing category.
- `set_category_description` — set or clear a category's description.
- `set_category_order` — update a category's display sort order.
- `enable_category` / `disable_category` — show or hide a category in listings.
- `remove_category` — **DESTRUCTIVE**: permanently deletes the category record. Articles that were assigned to it lose their category association. Irreversible.

**Brief tools:**
- `get_daily_brief` — returns the latest ready daily brief (headline, intro, topics with what happened / why it matters / links). Returns `{"status": "no_brief"}` when no ready brief exists yet.
- `refresh_brief` — enqueues a new brief generation run. Returns `{"status": "queued"}` when enqueued or `{"status": "already_pending"}` when one is already in progress.

**Ops / diagnostics tools:**
- `pipeline_status` — snapshot of article counts by status, in-flight claims, and enabled/disabled source counts. Use as the first health check.
- `list_stuck` — list articles whose worker claim has expired (default: older than 600 s), indicating a crashed or stalled worker.
- `list_failures` — list articles in `failed_processing` or `failed_ranking`, optionally filtered by stage.
- `reap_stale_claims` — release stale article and brief claims so they become re-claimable.
- `retry_failed` — reset failed articles to their pending state so workers retry them; optionally scoped to a single article or stage.
- `rerank` — transition articles to `pending_ranking` so the summarize-rank service re-scores them (single article, all ready, or failed only).

**Resources:**
- `article://{id}` — a single article by id.
- `feed://{view}` — article list for a named view.
- `profile://interests` — the current interest profile text.
- `brief://today` — the latest ready daily brief.
- `status://pipeline` — quick pipeline health snapshot (article counts, in-flight, source counts).

**Prompts:**
- `whats_latest` — search for recent articles on a topic and summarize the results.
- `daily_brief` — fetch and present the daily brief with topics and links.
- `troubleshoot` — step-by-step guide for diagnosing and fixing a stalled pipeline using the ops tools above.

### Endpoint URL

The MCP endpoint is:

```
http://<pi-ip-or-tailscale-hostname>:<MCP_PORT><MCP_PATH>
```

With the defaults (`MCP_PORT=8765`, `MCP_PATH=/mcp`), that is:

```
http://<pi>:8765/mcp
```

### Authentication

There is **no app-level authentication** in v1. The tailnet is the trust boundary — the MCP port should only be reachable from devices on your Tailscale network. Do not expose it on a public interface.

Set `MCP_BIND=127.0.0.1` in `.env` to restrict the published port to the Pi itself, then use Tailscale Serve (see below) to provide HTTPS access over your tailnet.

### Tailscale Serve (recommended — HTTPS over your tailnet)

Use **Tailscale Serve** to expose the MCP endpoint to all your Tailscale-connected devices with automatic HTTPS:

```bash
sudo tailscale serve --bg --set-path /mcp 8765
```

This proxies `https://<hostname>.your-tailnet.ts.net/mcp` to the local MCP port. Point your agents at the HTTPS URL and set `MCP_BIND=127.0.0.1` in `.env` to prevent direct LAN access.

To check or remove the serve rule:
```bash
tailscale serve status
sudo tailscale serve --remove /mcp
```

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
curl -fsSL https://raw.githubusercontent.com/oscarrenalias/personal-aggregator/main/deploy/bootstrap.sh | sh
```

The same one-liner you used to install also upgrades: it **auto-detects** your existing
install (a configured `/opt/personal-aggregator/.env`), so it **skips the onboarding
prompts**, refreshes `docker-compose.prod.yml` / `install.sh` / the `aggregator` script
from the latest release, **pulls the latest images**, and restarts — leaving your `.env`
and the Postgres volume untouched. Use this when a release changes deploy files (new
services/ports, or a new helper like the `aggregator` CLI).

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
