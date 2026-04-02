#!/usr/bin/env bash
# =============================================================================
# PlanetFlow - Docker Compose update script
#
# Updates a Docker Compose deployment from the current git checkout.
#
# Usage:
#   bash scripts/update_compose.sh
#   bash scripts/update_compose.sh --branch main
#   bash scripts/update_compose.sh --no-pull
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
  bash scripts/update_compose.sh [--branch <name>] [--no-pull] [--help]

Options:
  --branch <name>   Git branch to update from. Default: main
  --no-pull         Skip git fetch/reset and use the current checkout as-is.
  --help            Show this help text.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
BRANCH="main"
DO_PULL="yes"
FITTINGS_SCOPE="esi-fittings.read_fittings.v1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            if [[ $# -lt 2 ]]; then
                log_error "--branch requires a branch name."
                usage
                exit 1
            fi
            BRANCH="$2"
            shift 2
            ;;
        --no-pull)
            DO_PULL="no"
            shift
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
echo -e "${BLUE}   PlanetFlow - Docker Compose Update         ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo ""

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

if [[ "${DO_PULL}" == "yes" ]]; then
    if ! command -v git &>/dev/null; then
        log_error "git was not found."
        exit 1
    fi
    if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
        log_error "${PROJECT_DIR} is not a git checkout. Use --no-pull or clone the repository."
        exit 1
    fi

    log_info "Updating repository to origin/${BRANCH}..."
    cd "${PROJECT_DIR}"
    git fetch origin "${BRANCH}"
    git checkout "${BRANCH}" 2>/dev/null || true
    git reset --hard "origin/${BRANCH}"
    log_ok "Repository updated"
fi

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
else
    log_warn "No .env file found in ${PROJECT_DIR}. Continuing without env patching."
fi

log_info "Pulling remote images where available..."
cd "${PROJECT_DIR}"
docker compose pull || log_warn "docker compose pull failed or no remote image is available; continuing with local build."

log_info "Building images..."
docker compose build

log_info "Restarting services..."
docker compose up -d

log_info "Running database migrations..."
docker compose exec app alembic upgrade head

log_ok "Docker Compose update completed"
echo ""
echo -e "${CYAN}Log check:${NC} docker compose logs -n 100 app"
echo -e "${CYAN}Worker logs:${NC} docker compose logs -n 100 celery_worker"
echo ""
