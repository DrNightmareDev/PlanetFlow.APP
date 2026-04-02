#!/usr/bin/env bash
# =============================================================================
# PlanetFlow - Linux update script
#
# Supported modes:
#   native  - updates a systemd/venv installation from the git checkout in /opt/planetflow
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

APP_DIR="/opt/planetflow"
APP_USER="planetflow"
SERVICE_NAME="planetflow"
MODE="native"
BRANCH="main"
FITTINGS_SCOPE="esi-fittings.read_fittings.v1"

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
echo -e "${BLUE}       PlanetFlow - Update (${MODE})          ${NC}"
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
    env_file="${PROJECT_DIR}/.env"
    if [[ -f "${env_file}" ]]; then
        current_scopes=$(grep "^EVE_SCOPES=" "${env_file}" | cut -d= -f2- || true)
        if [[ -n "${current_scopes}" && " ${current_scopes} " != *" ${FITTINGS_SCOPE} "* ]]; then
            updated_scopes="${current_scopes} ${FITTINGS_SCOPE}"
            python3 - "${env_file}" "${updated_scopes}" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
new_value = sys.argv[2]
text = path.read_text(encoding="utf-8")
lines = text.splitlines()
for idx, line in enumerate(lines):
    if line.startswith("EVE_SCOPES="):
        lines[idx] = f"EVE_SCOPES={new_value}"
        break
path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
PY
            log_ok "Added fittings scope to ${env_file}"
        fi
    fi

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
    echo -e "${CYAN}Log check:${NC} docker compose logs -n 100 app celery_worker celery_beat celery_ws"
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

env_file="${APP_DIR}/.env"
if [[ -f "${env_file}" ]]; then
    current_scopes=$(grep "^EVE_SCOPES=" "${env_file}" | cut -d= -f2- || true)
    if [[ -n "${current_scopes}" && " ${current_scopes} " != *" ${FITTINGS_SCOPE} "* ]]; then
        updated_scopes="${current_scopes} ${FITTINGS_SCOPE}"
        python3 - "${env_file}" "${updated_scopes}" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
new_value = sys.argv[2]
text = path.read_text(encoding="utf-8")
lines = text.splitlines()
for idx, line in enumerate(lines):
    if line.startswith("EVE_SCOPES="):
        lines[idx] = f"EVE_SCOPES={new_value}"
        break
path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
PY
        log_ok "Added fittings scope to ${env_file}"
    fi
fi

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

log_info "Restarting services..."
# Restart all services (web + worker + beat + ws); ignore if worker/beat/ws don't exist yet
systemctl restart "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}-worker" 2>/dev/null || log_warn "Worker service not found — skipping (run setup_linux.sh to install it)"
systemctl restart "${SERVICE_NAME}-beat"   2>/dev/null || log_warn "Beat service not found — skipping (run setup_linux.sh to install it)"
systemctl restart "${SERVICE_NAME}-ws"     2>/dev/null || log_warn "WS service not found - skipping (run setup_linux.sh or upgrade_to_latest.sh to install it)"
sleep 3

APP_STATUS="$(systemctl is-active "${SERVICE_NAME}"        2>/dev/null || echo "failed")"
WRK_STATUS="$(systemctl is-active "${SERVICE_NAME}-worker" 2>/dev/null || echo "n/a")"
BET_STATUS="$(systemctl is-active "${SERVICE_NAME}-beat"   2>/dev/null || echo "n/a")"
WS_STATUS="$(systemctl is-active "${SERVICE_NAME}-ws"      2>/dev/null || echo "n/a")"

_svc() { [ "$2" = "active" ] && echo -e "  $1 ${GREEN}active${NC}" || echo -e "  $1 ${RED}$2${NC}"; }

echo ""
echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}              Update completed                    ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo ""
_svc "Web (gunicorn):  " "$APP_STATUS"
_svc "Celery Worker:   " "$WRK_STATUS"
_svc "Celery Beat:     " "$BET_STATUS"
_svc "Celery WS:       " "$WS_STATUS"
echo ""
echo -e "${CYAN}Logs:${NC}"
echo -e "  Web:    journalctl -u ${SERVICE_NAME} -f"
echo -e "  Worker: journalctl -u ${SERVICE_NAME}-worker -f"
echo -e "  Beat:   journalctl -u ${SERVICE_NAME}-beat -f"
echo -e "  WS:     journalctl -u ${SERVICE_NAME}-ws -f"
echo ""
