#!/usr/bin/env sh
# Personal Aggregator — one-line bootstrap installer.
#
# Fetches the latest release assets, walks you through a tiny onboarding
# (LLM key + LAN exposure), and runs the installer.
#
#   curl -fsSL https://raw.githubusercontent.com/oscarrenalias/personal-aggregator/main/deploy/bootstrap.sh | sh
#
# Non-interactive (CI / re-runs) — provide answers via env vars and skip prompts:
#
#   curl -fsSL .../bootstrap.sh | NONINTERACTIVE=1 OPENAI_API_KEY=sk-... WEB_BIND=0.0.0.0 sh
#
# Honoured env vars: OPENAI_API_KEY, ANTHROPIC_API_KEY, WEB_BIND, NONINTERACTIVE.
set -eu

REPO="oscarrenalias/personal-aggregator"
BASE="https://github.com/${REPO}/releases/latest/download"
ASSETS="install.sh docker-compose.prod.yml aggregator.service env.example aggregator"
INSTALL_DIR="/opt/personal-aggregator"
TTY="/dev/tty"

say()  { printf '\033[1;36m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[bootstrap] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Prompt only when interactive (a terminal is attached and NONINTERACTIVE is unset).
interactive() { [ -z "${NONINTERACTIVE:-}" ] && [ -r "$TTY" ]; }

# ── Prerequisites ────────────────────────────────────────────────────────────
command -v curl   >/dev/null 2>&1 || die "curl is required."
command -v docker >/dev/null 2>&1 || die "docker is required — install it first: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "the Docker Compose plugin is required (docker compose ...)."

# install.sh writes /opt and a systemd unit, so it needs root.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "run as root, or install sudo."
    SUDO="sudo"
fi

# ── Download the latest release assets ───────────────────────────────────────
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
say "Fetching latest release assets from ${REPO}…"
for a in $ASSETS; do
    curl -fsSL -o "${WORK}/${a}" "${BASE}/${a}" \
        || die "could not download '${a}'. Is there a published release yet?"
done
chmod +x "${WORK}/install.sh"
say "Got: ${ASSETS}"

# ── Onboarding ───────────────────────────────────────────────────────────────
LLM_KEY="${OPENAI_API_KEY:-}"
LLM_KEY_NAME="OPENAI_API_KEY"
if [ -z "$LLM_KEY" ] && [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    LLM_KEY="$ANTHROPIC_API_KEY"; LLM_KEY_NAME="ANTHROPIC_API_KEY"
fi
WEB_BIND_VAL="${WEB_BIND:-0.0.0.0}"

if interactive; then
    if [ -z "$LLM_KEY" ]; then
        printf 'LLM API key for article summarization (OpenAI sk-… ; Enter to set later): ' > "$TTY"
        stty -echo < "$TTY" 2>/dev/null || true
        read LLM_KEY < "$TTY" || LLM_KEY=""
        stty echo < "$TTY" 2>/dev/null || true
        printf '\n' > "$TTY"
    fi
    printf 'Expose the web UI to your whole home LAN? [Y/n] ' > "$TTY"
    read _ans < "$TTY" || _ans=""
    case "$_ans" in
        [Nn]*) WEB_BIND_VAL="127.0.0.1"; say "UI will be Pi-local only (front it with Tailscale for remote access)." ;;
        *)     WEB_BIND_VAL="0.0.0.0" ;;
    esac
fi

# ── Install ──────────────────────────────────────────────────────────────────
say "Installing the stack (you may be prompted for your sudo password)…"
( cd "$WORK" && $SUDO ./install.sh install )

# ── Apply onboarding answers to the installed .env, then restart ─────────────
ENV_FILE="${INSTALL_DIR}/.env"
set_env() { # set_env KEY VALUE
    _k="$1"; _v="$2"
    if $SUDO grep -qE "^[# ]*${_k}=" "$ENV_FILE" 2>/dev/null; then
        $SUDO sed -i "s|^[# ]*${_k}=.*|${_k}=${_v}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$_k" "$_v" | $SUDO tee -a "$ENV_FILE" >/dev/null
    fi
}

CHANGED=0
if [ -n "$LLM_KEY" ]; then set_env "$LLM_KEY_NAME" "$LLM_KEY"; CHANGED=1; say "Wrote ${LLM_KEY_NAME} to ${ENV_FILE}"; fi
if [ "$WEB_BIND_VAL" != "0.0.0.0" ]; then set_env "WEB_BIND" "$WEB_BIND_VAL"; CHANGED=1; fi

if [ "$CHANGED" -eq 1 ]; then
    say "Applying configuration…"
    ( cd "$INSTALL_DIR" && $SUDO docker compose -f docker-compose.prod.yml up -d )
fi

# ── Done ─────────────────────────────────────────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done. The web UI is starting up."
if [ "$WEB_BIND_VAL" = "0.0.0.0" ] && [ -n "$IP" ]; then
    say "Open it from any device on your network:  http://${IP}:8000/"
else
    say "Open it on this machine:  http://127.0.0.1:8000/"
fi
[ -z "$LLM_KEY" ] && warn "No LLM key set — edit ${ENV_FILE} (OPENAI_API_KEY=…) and run: sudo systemctl restart aggregator"
say "Manage it with the 'aggregator' CLI (installed to /usr/local/bin), e.g.:"
say "  sudo aggregator sources add -n 'BBC News' -u 'http://feeds.bbci.co.uk/news/rss.xml'"
say "  sudo aggregator sources import-opml your-feedly.opml     # import from Feedly"
say "  sudo aggregator profile set 'My interests…'              # tune ranking"
say "  sudo aggregator --help"
