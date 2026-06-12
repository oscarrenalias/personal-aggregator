#!/usr/bin/env bash
# Idempotent installer for the personal-aggregator stack on Raspberry Pi OS (arm64).
# Usage: install.sh [--check] [install|update]
set -euo pipefail

INSTALL_DIR="/opt/personal-aggregator"
SERVICE_NAME="aggregator"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
IMAGE_PREFIX="ghcr.io/oscarrenalias/personal-aggregator"
APP_VERSION="latest"

_sd="$(dirname -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -- "${_sd}" && pwd)"

DRY_RUN=false
SUBCOMMAND=""

log()  { echo "[install.sh] $*"; }
plan() { echo "  [plan] $*"; }
err()  { echo "[install.sh] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    cat <<'EOF'
Usage: install.sh [--check] [install|update]

Commands:
  install   (default) Install the aggregator stack and enable on boot.
  update    Pull latest images and restart the running stack.

Flags:
  --check, -n   Dry run: validate prerequisites and print planned actions; no changes made.
  --help,  -h   Show this help message.
EOF
}

# ── Argument parsing ─────────────────────────────────────────────────────────

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check|-n) DRY_RUN=true ;;
            install)    SUBCOMMAND="install" ;;
            update)     SUBCOMMAND="update" ;;
            --help|-h)  usage; exit 0 ;;
            *) die "Unknown argument: $1. Run with --help for usage." ;;
        esac
        shift
    done
    SUBCOMMAND="${SUBCOMMAND:-install}"
}

# ── Asset resolution ─────────────────────────────────────────────────────────

# Locate a release asset file.
# Checks SCRIPT_DIR first (flat release download layout), then SCRIPT_DIR/..
# (for running install.sh from deploy/ inside the repo).
find_asset() {
    # Accepts one or more candidate names; returns the first that exists.
    local name parent
    parent="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
    for name in "$@"; do
        if [[ -f "${SCRIPT_DIR}/${name}" ]]; then
            echo "${SCRIPT_DIR}/${name}"
            return 0
        fi
        if [[ -f "${parent}/${name}" ]]; then
            echo "${parent}/${name}"
            return 0
        fi
    done
    return 1
}

# The env template ships in-repo as .env.example, but GitHub release assets
# can't start with a dot, so it's published as env.example. Accept all variants.
ENV_EXAMPLE_NAMES=(env.example .env.example default.env.example)

check_assets() {
    local errors=0
    local asset
    for asset in docker-compose.prod.yml aggregator.service; do
        if ! find_asset "${asset}" > /dev/null 2>&1; then
            err "Required asset not found: ${asset}"
            errors=$((errors + 1))
        fi
    done
    if ! find_asset "${ENV_EXAMPLE_NAMES[@]}" > /dev/null 2>&1; then
        err "Required asset not found: env.example (or .env.example)"
        errors=$((errors + 1))
    fi
    return "${errors}"
}

# ── Prerequisites ────────────────────────────────────────────────────────────

check_docker() {
    if ! command -v docker > /dev/null 2>&1; then
        die "Docker is not installed. Install Docker Engine first:
  https://docs.docker.com/engine/install/raspberry-pi-os/"
    fi
}

# ── Helpers ──────────────────────────────────────────────────────────────────

# Idempotently set KEY=VALUE in an env file.
set_env_var() {
    local key="$1"
    local value="$2"
    local file="$3"
    if grep -q "^${key}=" "${file}" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
    else
        echo "${key}=${value}" >> "${file}"
    fi
}

# ── update subcommand ────────────────────────────────────────────────────────

do_update() {
    check_docker

    if [[ ! -d "${INSTALL_DIR}" ]]; then
        die "Installation directory ${INSTALL_DIR} not found. Run 'install.sh install' first."
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        log "DRY RUN — no changes will be made."
        plan "cd ${INSTALL_DIR}"
        plan "docker compose -f docker-compose.prod.yml pull"
        plan "docker compose -f docker-compose.prod.yml up -d"
        exit 0
    fi

    log "Pulling latest images..."
    (cd "${INSTALL_DIR}" && docker compose -f docker-compose.prod.yml pull)
    log "Restarting stack..."
    (cd "${INSTALL_DIR}" && docker compose -f docker-compose.prod.yml up -d)
    log "Update complete."
}

# ── install subcommand ───────────────────────────────────────────────────────

do_install() {
    check_docker

    if ! check_assets; then
        die "One or more required assets are missing (see above)."
    fi

    local compose_src env_src service_src
    compose_src="$(find_asset docker-compose.prod.yml)"
    env_src="$(find_asset "${ENV_EXAMPLE_NAMES[@]}")"
    service_src="$(find_asset aggregator.service)"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log "DRY RUN — no changes will be made."
        echo
        echo "Prerequisites:"
        plan "Docker: $(docker --version)"
        plan "Assets: docker-compose.prod.yml, .env.example, aggregator.service — all found"
        echo
        echo "Planned actions:"
        plan "Create directory (if absent): ${INSTALL_DIR}"
        plan "Copy ${compose_src} -> ${INSTALL_DIR}/docker-compose.prod.yml"
        if [[ -f "${INSTALL_DIR}/.env" ]]; then
            plan "Preserve existing ${INSTALL_DIR}/.env (will NOT overwrite)"
        else
            plan "Copy ${env_src} -> ${INSTALL_DIR}/.env"
        fi
        plan "Set IMAGE_PREFIX=${IMAGE_PREFIX} in ${INSTALL_DIR}/.env"
        plan "Set APP_VERSION=${APP_VERSION} in ${INSTALL_DIR}/.env"
        plan "Install aggregator CLI -> ${INSTALL_DIR}/aggregator (if asset present)"
        plan "docker compose pull (refresh to latest images)"
        plan "Install ${service_src} -> ${SERVICE_FILE}"
        plan "systemctl daemon-reload"
        plan "systemctl enable ${SERVICE_NAME}"
        plan "systemctl start ${SERVICE_NAME}"
        exit 0
    fi

    # Create install directory
    if [[ ! -d "${INSTALL_DIR}" ]]; then
        log "Creating ${INSTALL_DIR}..."
        mkdir -p "${INSTALL_DIR}"
    fi

    # Copy compose file (always refresh to pick up updates)
    log "Copying docker-compose.prod.yml -> ${INSTALL_DIR}/"
    cp "${compose_src}" "${INSTALL_DIR}/docker-compose.prod.yml"

    # Copy .env only if absent -- preserve customised config on re-runs
    if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
        log "Creating ${INSTALL_DIR}/.env from .env.example"
        cp "${env_src}" "${INSTALL_DIR}/.env"
    else
        log "${INSTALL_DIR}/.env already exists -- skipping copy (configuration preserved)"
    fi

    # Ensure IMAGE_PREFIX and APP_VERSION are set (add or update)
    set_env_var "IMAGE_PREFIX" "${IMAGE_PREFIX}" "${INSTALL_DIR}/.env"
    set_env_var "APP_VERSION"  "${APP_VERSION}"  "${INSTALL_DIR}/.env"

    # Install the `aggregator` admin CLI wrapper to /usr/local/bin (optional asset)
    local wrapper_src
    wrapper_src="$(find_asset aggregator 2>/dev/null || true)"
    if [[ -n "${wrapper_src}" ]]; then
        log "Installing aggregator CLI -> ${INSTALL_DIR}/aggregator"
        cp "${wrapper_src}" "${INSTALL_DIR}/aggregator"
        chmod +x "${INSTALL_DIR}/aggregator"
    else
        log "aggregator CLI wrapper not in assets -- skipping (run admin via 'docker compose run --rm admin')"
    fi

    # Pull the latest images so a re-run acts as a full upgrade. (On a first install
    # this is effectively what `up -d` would do anyway; on a re-run it refreshes
    # :latest images that `up -d` alone would not re-fetch.)
    log "Pulling latest images..."
    (cd "${INSTALL_DIR}" && docker compose -f docker-compose.prod.yml pull)

    # Install systemd service unit
    log "Installing ${service_src} -> ${SERVICE_FILE}"
    cp "${service_src}" "${SERVICE_FILE}"

    log "Reloading systemd daemon..."
    systemctl daemon-reload

    log "Enabling ${SERVICE_NAME}.service..."
    systemctl enable "${SERVICE_NAME}"

    # Bring the stack up via compose directly, then register the unit as active.
    # The unit is Type=oneshot + RemainAfterExit=yes: on an upgrade it is already
    # "active (exited)", and in that state `systemctl start` is a no-op that would
    # NOT start services newly added to the compose file (e.g. brief/mcp). Running
    # `up -d` here is idempotent — it starts new/changed services and leaves
    # unchanged ones running — so upgrades pick up new services without a manual
    # restart, while a fresh install still starts everything.
    log "Starting/refreshing the stack (docker compose up -d)..."
    (cd "${INSTALL_DIR}" && docker compose -f docker-compose.prod.yml up -d)

    log "Marking ${SERVICE_NAME}.service active..."
    systemctl start "${SERVICE_NAME}" || true

    log ""
    log "Installation complete."
    log "  Stack directory : ${INSTALL_DIR}"
    log "  Env file        : ${INSTALL_DIR}/.env"
    log "  Service unit    : ${SERVICE_FILE}"
    log ""
    log "IMPORTANT: Edit ${INSTALL_DIR}/.env and set OPENAI_API_KEY (or ANTHROPIC_API_KEY)"
    log "and other required vars, then restart: systemctl restart ${SERVICE_NAME}"
}

# ── Main ─────────────────────────────────────────────────────────────────────

parse_args "$@"

case "${SUBCOMMAND}" in
    install) do_install ;;
    update)  do_update ;;
    *)       die "Unknown subcommand: ${SUBCOMMAND}. Run with --help for usage." ;;
esac
