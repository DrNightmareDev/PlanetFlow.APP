#!/usr/bin/env bash
# =============================================================================
# EVE PI Manager - Universal Upgrade Script
#
# Brings any previous version to the current release.
# Handles: RabbitMQ install, new systemd units, .env patching,
#          pip deps, alembic migrations, and Docker Compose rebuilds.
#
# Usage (native Linux):   sudo bash scripts/upgrade_to_latest.sh
# Usage (Docker Compose): bash scripts/upgrade_to_latest.sh --compose
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
log_step()  { echo -e "\n${BLUE}── $1 ${NC}"; }

APP_DIR="/opt/eve-pi-manager"
APP_USER="evepi"
SERVICE_NAME="eve-pi-manager"
MODE="native"
BRANCH="main"

usage() {
    cat << 'EOF'
Usage:
  sudo bash scripts/upgrade_to_latest.sh [--compose] [--branch <name>]

Options:
  --compose         Upgrade a Docker Compose deployment instead of native.
  --branch <name>   Git branch to pull from (default: main).
  --help            Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --compose) MODE="compose"; shift ;;
        --branch)
            [[ $# -lt 2 ]] && { log_error "--branch requires a name."; usage; exit 1; }
            BRANCH="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     EVE PI Manager — Universal Upgrade       ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Helper: add a line to .env if the key is missing ─────────────────────────
env_add_if_missing() {
    local file="$1" key="$2" value="$3"
    if ! grep -q "^${key}=" "${file}" 2>/dev/null; then
        echo "${key}=${value}" >> "${file}"
        log_ok ".env: added ${key}"
    fi
}

# =============================================================================
# DOCKER COMPOSE MODE
# =============================================================================
if [[ "${MODE}" == "compose" ]]; then
    if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null; then
        log_error "docker / docker compose not found."
        exit 1
    fi

    log_step "Pull latest code"
    cd "${PROJECT_DIR}"
    git fetch origin "${BRANCH}"
    git reset --hard "origin/${BRANCH}"
    log_ok "Branch origin/${BRANCH} pulled"

    log_step "Patch .env (add missing variables)"
    ENV_FILE="${PROJECT_DIR}/.env"
    if [[ ! -f "${ENV_FILE}" ]]; then
        cp "${PROJECT_DIR}/.env.example" "${ENV_FILE}"
        log_warn ".env was missing — copied from .env.example. Please fill in EVE credentials."
    fi
    env_add_if_missing "${ENV_FILE}" "RABBITMQ_USER"    "evepi"
    env_add_if_missing "${ENV_FILE}" "RABBITMQ_PASS"    "change_me_rabbit"
    env_add_if_missing "${ENV_FILE}" "CELERY_BROKER_URL" "amqp://evepi:change_me_rabbit@rabbitmq:5672//"
    env_add_if_missing "${ENV_FILE}" "WEB_WORKERS"      "4"
    env_add_if_missing "${ENV_FILE}" "SENTRY_DSN"       ""
    env_add_if_missing "${ENV_FILE}" "FLOWER_USER"      "admin"
    env_add_if_missing "${ENV_FILE}" "FLOWER_PASS"      "change_me_flower"

    log_step "Rebuild and restart containers"
    docker compose build
    docker compose up -d

    log_step "Run database migrations"
    docker compose exec app alembic upgrade head
    log_ok "Migrations completed"

    echo ""
    echo -e "${GREEN}Docker Compose upgrade complete.${NC}"
    echo -e "${CYAN}Logs:${NC} docker compose logs -f app"
    echo ""
    exit 0
fi

# =============================================================================
# NATIVE (systemd) MODE
# =============================================================================
if [[ $EUID -ne 0 ]]; then
    log_error "Native upgrade must be run as root."
    exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
    log_error "${APP_DIR} not found. Run setup_linux.sh first."
    exit 1
fi

ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    log_error "Missing ${ENV_FILE}. Cannot upgrade without it."
    exit 1
fi

# ── Step 1: Pull code ─────────────────────────────────────────────────────────
log_step "Pull latest code → ${APP_DIR}"
cd "${APP_DIR}"
git fetch origin "${BRANCH}"
git checkout "${BRANCH}" 2>/dev/null || true
git reset --hard "origin/${BRANCH}"
git clean -fd -e .env -e data/ -e venv/ -e .cache/
log_ok "origin/${BRANCH} applied"

# ── Step 2: Install RabbitMQ (idempotent) ─────────────────────────────────────
log_step "RabbitMQ"
if ! command -v rabbitmqctl &>/dev/null; then
    log_info "RabbitMQ not found — installing..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rabbitmq-server
    log_ok "RabbitMQ installed"
else
    log_ok "RabbitMQ already installed"
fi

systemctl enable rabbitmq-server --quiet
systemctl start  rabbitmq-server || true

# ── Step 3: Patch .env (add missing keys) ────────────────────────────────────
log_step "Patch ${ENV_FILE}"

# Generate or reuse RabbitMQ password
RABBITMQ_PASS_VAL=""
_existing=$(grep "^RABBITMQ_PASS=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
if [[ -n "$_existing" && "$_existing" != "change_me_rabbit" ]]; then
    RABBITMQ_PASS_VAL="$_existing"
    log_ok ".env: RABBITMQ_PASS already set"
else
    RABBITMQ_PASS_VAL=$(openssl rand -base64 24 | tr -d '/+=\n' | head -c 24)
    log_info "Generated new RabbitMQ password"
fi

env_add_if_missing "${ENV_FILE}" "RABBITMQ_USER"     "evepi"
# Replace placeholder or add if missing
if grep -q "^RABBITMQ_PASS=change_me_rabbit" "${ENV_FILE}"; then
    sed -i "s|^RABBITMQ_PASS=.*|RABBITMQ_PASS=${RABBITMQ_PASS_VAL}|" "${ENV_FILE}"
    log_ok ".env: RABBITMQ_PASS updated"
else
    env_add_if_missing "${ENV_FILE}" "RABBITMQ_PASS" "${RABBITMQ_PASS_VAL}"
fi
env_add_if_missing "${ENV_FILE}" "CELERY_BROKER_URL" "amqp://evepi:${RABBITMQ_PASS_VAL}@localhost:5672//"
env_add_if_missing "${ENV_FILE}" "WEB_WORKERS"       "4"
env_add_if_missing "${ENV_FILE}" "SENTRY_DSN"        ""
chmod 600 "${ENV_FILE}"

# ── Step 4: Set up RabbitMQ user ─────────────────────────────────────────────
log_step "RabbitMQ user 'evepi'"
rabbitmqctl add_user "evepi" "${RABBITMQ_PASS_VAL}" 2>/dev/null || \
    rabbitmqctl change_password "evepi" "${RABBITMQ_PASS_VAL}"
rabbitmqctl set_permissions -p "/" "evepi" ".*" ".*" ".*" 2>/dev/null || true
log_ok "RabbitMQ user configured"

# ── Step 5: File permissions ──────────────────────────────────────────────────
log_step "File permissions"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
find "${APP_DIR}" -type d -exec chmod 755 {} \;
find "${APP_DIR}" -type f -exec chmod 644 {} \;
chmod 755 "${APP_DIR}/venv/bin/"* 2>/dev/null || true
chmod +x  "${APP_DIR}/scripts/"*.sh 2>/dev/null || true
chmod 600 "${ENV_FILE}"
log_ok "Permissions set"

# ── Step 6: Python dependencies ──────────────────────────────────────────────
log_step "Python dependencies"
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
log_ok "Dependencies up to date"

# ── Step 7: Database migrations ──────────────────────────────────────────────
log_step "Alembic migrations"
cd "${APP_DIR}"
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/alembic" upgrade head
log_ok "Migrations complete"

# ── Step 8: Update / create systemd units ────────────────────────────────────
log_step "Systemd units"

# Resolve WEB_WORKERS from .env at script time — systemd doesn't support ${VAR:-default} syntax
WEB_WORKERS_VAL=$(grep "^WEB_WORKERS=" "${ENV_FILE}" | cut -d= -f2- | tr -d '[:space:]')
WEB_WORKERS_VAL="${WEB_WORKERS_VAL:-4}"

# Web — upgrade from uvicorn to gunicorn if needed
if grep -q "uvicorn app.main:app" "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null && \
   ! grep -q "gunicorn" "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null; then
    log_info "Upgrading web service from uvicorn → gunicorn..."
fi

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=EVE PI Manager - Web
After=postgresql.service rabbitmq-server.service network.target
Wants=postgresql.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/venv/bin/gunicorn app.main:app \\
    -k uvicorn.workers.UvicornWorker \\
    --workers ${WEB_WORKERS_VAL} \\
    --bind 127.0.0.1:8000 \\
    --timeout 120 \\
    --access-logfile - \\
    --error-logfile -
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${SERVICE_NAME}-worker.service" << EOF
[Unit]
Description=EVE PI Manager - Celery Worker
After=rabbitmq-server.service postgresql.service network.target
Wants=rabbitmq-server.service postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/venv/bin/celery -A app.celery_app worker \\
    --loglevel=info --concurrency=4 -Q celery
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}-worker
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${SERVICE_NAME}-beat.service" << EOF
[Unit]
Description=EVE PI Manager - Celery Beat
After=rabbitmq-server.service postgresql.service network.target
Wants=rabbitmq-server.service postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/venv/bin/celery -A app.celery_app beat \\
    --loglevel=info --schedule=/tmp/${SERVICE_NAME}-beat-schedule
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}-beat
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" "${SERVICE_NAME}-worker" "${SERVICE_NAME}-beat" --quiet
log_ok "Systemd units updated"

# ── Step 9: Restart all services ─────────────────────────────────────────────
log_step "Restart services"
systemctl restart "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}-worker"
systemctl restart "${SERVICE_NAME}-beat"
sleep 3

APP_STATUS=$(systemctl is-active "${SERVICE_NAME}"        2>/dev/null || echo "failed")
WRK_STATUS=$(systemctl is-active "${SERVICE_NAME}-worker" 2>/dev/null || echo "failed")
BET_STATUS=$(systemctl is-active "${SERVICE_NAME}-beat"   2>/dev/null || echo "failed")
RMQ_STATUS=$(systemctl is-active rabbitmq-server          2>/dev/null || echo "failed")
PG_STATUS=$(systemctl is-active  postgresql               2>/dev/null || echo "failed")

_svc() {
    local label="$1" status="$2"
    if [[ "$status" == "active" ]]; then
        echo -e "  $(printf '%-22s' "$label") ${GREEN}active${NC}"
    else
        echo -e "  $(printf '%-22s' "$label") ${RED}${status}${NC}"
    fi
}

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Upgrade completed                  ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Services:${NC}"
_svc "PostgreSQL"          "$PG_STATUS"
_svc "RabbitMQ"            "$RMQ_STATUS"
_svc "Web (gunicorn)"      "$APP_STATUS"
_svc "Celery Worker"       "$WRK_STATUS"
_svc "Celery Beat"         "$BET_STATUS"
echo ""
echo -e "  ${CYAN}Logs:${NC}"
echo -e "  Web:    journalctl -u ${SERVICE_NAME} -f"
echo -e "  Worker: journalctl -u ${SERVICE_NAME}-worker -f"
echo -e "  Beat:   journalctl -u ${SERVICE_NAME}-beat -f"
echo ""

if [[ "$APP_STATUS" != "active" ]]; then
    echo -e "  ${RED}Web service failed to start. Check logs:${NC}"
    echo -e "  journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
    echo ""
fi
