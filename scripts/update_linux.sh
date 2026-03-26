#!/usr/bin/env bash
# =============================================================================
# EVE PI Manager - Linux update script
#
# Supported modes:
#   native  - updates a systemd/venv installation from the git checkout in /opt/eve-pi-manager
#   compose - updates a Docker Compose installation from the current checkout
#
# Usage:
#   bash scripts/update_linux.sh
#   bash scripts/update_linux.sh --compose
#   bash scripts/update_linux.sh --branch develop
#   bash scripts/update_linux.sh --compose --branch main
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
    cat <<'EOF'
Usage:
  bash scripts/update_linux.sh [--compose] [--branch <name>] [--help]

Options:
  --compose         Update the current Docker Compose deployment.
  --branch <name>   Git branch to update from. Default: main
  --help            Show this help text.
EOF
}

APP_DIR="/opt/eve-pi-manager"
APP_USER="evepi"
SERVICE_NAME="eve-pi-manager"
MODE="native"
BRANCH="main"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --compose)
            MODE="compose"
            shift
            ;;
        --branch)
            if [[ $# -lt 2 ]]; then
                log_error "--branch requires a branch name."
                usage
                exit 1
            fi
            BRANCH="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo ""
echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}       EVE PI Manager - Update (${MODE})          ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

log_info "Updating repository to origin/${BRANCH}..."
cd "${PROJECT_DIR}"
git fetch origin "${BRANCH}"
git reset --hard "origin/${BRANCH}"
log_ok "Repository updated"

if [[ "${MODE}" == "compose" ]]; then
    if ! command -v docker &>/dev/null; then
        log_error "docker was not found."
        exit 1
    fi
    if ! docker compose version &>/dev/null; then
        log_error "docker compose was not found."
        exit 1
    fi
    if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
        log_error "docker-compose.yml was not found in ${PROJECT_DIR}."
        exit 1
    fi

    log_info "Running Docker Compose update..."
    cd "${PROJECT_DIR}"
    docker compose pull || log_warn "docker compose pull failed or no remote image is available; continuing with local build."
    docker compose build
    docker compose up -d
    docker compose exec app alembic upgrade head
    log_ok "Docker Compose update completed"

    echo ""
    echo -e "${CYAN}Log check:${NC} docker compose logs -n 100 app"
    echo ""
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root in native mode."
    exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
    log_error "${APP_DIR} was not found. Run setup_linux.sh first."
    exit 1
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
    log_error "Missing ${APP_DIR}/.env"
    exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
    log_error "${APP_DIR} is not a git checkout. Re-run setup_linux.sh or clone the repository first."
    exit 1
fi

log_info "Updating git checkout in ${APP_DIR} to origin/${BRANCH}..."
cd "${APP_DIR}"
git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git reset --hard "origin/${BRANCH}"
git clean -fd \
    -e .env \
    -e data/ \
    -e venv/ \
    -e .cache/
log_ok "Git checkout updated"

log_info "Setting ownership and file permissions..."
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
find "${APP_DIR}" -type d -exec chmod 755 {} \;
find "${APP_DIR}" -type f -exec chmod 644 {} \;
chmod 755 "${APP_DIR}/venv/bin/"* 2>/dev/null || true
chmod 600 "${APP_DIR}/.env"
log_ok "Permissions updated"

log_info "Updating Python dependencies..."
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
log_ok "Dependencies updated"

log_info "Running database migrations..."
cd "${APP_DIR}"
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/alembic" upgrade head
log_ok "Migrations completed"

log_info "Restarting service ${SERVICE_NAME}..."
systemctl restart "${SERVICE_NAME}"
sleep 2
STATUS="$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || echo "failed")"

echo ""
echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}              Update completed                    ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo ""
echo -e "Service: $([ "${STATUS}" = "active" ] && echo -e "${GREEN}active${NC}" || echo -e "${RED}${STATUS}${NC}")"
echo -e "${CYAN}Log check:${NC} journalctl -u ${SERVICE_NAME} -f"
echo ""
