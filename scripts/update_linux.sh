#!/usr/bin/env bash
# =============================================================================
# EVE PI Manager - Update-Skript (Linux)
# Aktualisiert App-Dateien, Abhängigkeiten und Datenbank-Migrationen.
# .env bleibt vollständig erhalten.
# Verwendung: bash scripts/update_linux.sh  (als root auf Linux)
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

APP_DIR="/opt/eve-pi-manager"
APP_USER="evepi"
SERVICE_NAME="eve-pi-manager"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       EVE PI Manager - Update Linux          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Root-Prüfung
if [[ $EUID -ne 0 ]]; then
    log_error "Dieses Skript muss als root ausgeführt werden!"
    exit 1
fi

# App-Verzeichnis prüfen
if [[ ! -d "${APP_DIR}" ]]; then
    log_error "${APP_DIR} nicht gefunden. Bitte zuerst setup_linux.sh ausführen."
    exit 1
fi

# ============ 0. Abhängigkeiten prüfen ============
if ! command -v rsync &>/dev/null; then
    log_info "rsync nicht gefunden, installiere..."
    apt-get install -y -qq rsync
fi

# ============ 1. Git Pull ============
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

log_info "Aktualisiere Repository..."
cd "${PROJECT_DIR}"
git fetch origin main
git reset --hard origin/main
log_ok "Repository aktualisiert"

# ============ 2. .env sichern ============
log_info "Sichere .env..."
ENV_BACKUP=$(mktemp /tmp/eve-pi-env-backup.XXXXXX)
cp "${APP_DIR}/.env" "${ENV_BACKUP}"
log_ok ".env gesichert → ${ENV_BACKUP}"

# ============ 3. Neue Dateien einspielen ============
log_info "Kopiere aktualisierte App-Dateien nach ${APP_DIR}..."
rsync -a --exclude='.env' \
         --exclude='venv/' \
         --exclude='data/' \
         --exclude='__pycache__/' \
         --exclude='*.pyc' \
         --exclude='.git/' \
         "${PROJECT_DIR}/" "${APP_DIR}/"
log_ok "Dateien aktualisiert"

# ============ 4. .env wiederherstellen ============
cp "${ENV_BACKUP}" "${APP_DIR}/.env"
chmod 600 "${APP_DIR}/.env"
rm -f "${ENV_BACKUP}"
log_ok ".env wiederhergestellt"

# ============ 5. Dateirechte setzen ============
log_info "Setze Dateirechte..."
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
find "${APP_DIR}" -type d -exec chmod 755 {} \;
find "${APP_DIR}" -type f -exec chmod 644 {} \;
chmod 755 "${APP_DIR}/venv/bin/"* 2>/dev/null || true
chmod 600 "${APP_DIR}/.env"
log_ok "Dateirechte gesetzt"

# ============ 6. Python-Abhängigkeiten aktualisieren ============
log_info "Aktualisiere Python-Abhängigkeiten..."
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
log_ok "Abhängigkeiten aktualisiert"

# ============ 7. Datenbank-Migrationen ============
log_info "Führe Datenbank-Migrationen aus..."
cd "${APP_DIR}"
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/alembic" upgrade head
log_ok "Migrationen abgeschlossen"

# ============ 8. Service neu starten ============
log_info "Starte Service neu..."
systemctl restart "${SERVICE_NAME}"
sleep 2
STATUS=$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || echo "failed")

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Update abgeschlossen               ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Service: $([ "$STATUS" = "active" ] && echo -e "${GREEN}aktiv${NC}" || echo -e "${RED}${STATUS}${NC}")"
echo ""
echo -e "  ${CYAN}Logs:${NC}  journalctl -u ${SERVICE_NAME} -f"
echo ""
