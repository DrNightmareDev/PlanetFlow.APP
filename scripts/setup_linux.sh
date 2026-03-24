#!/usr/bin/env bash
# =============================================================================
# EVE PI Manager - Vollständiges Linux Installations-Skript
# Getestet auf: Debian 12 (Bookworm)
# Verwendung: Dieses Skript als root auf einem Linux-Host oder in einem Linux-Container ausführen
# =============================================================================

set -euo pipefail

# Farben für Ausgabe
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

APP_DIR="/opt/eve-pi-manager"
APP_USER="evepi"
APP_PORT="8000"
SERVICE_NAME="eve-pi-manager"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       EVE PI Manager - Setup Linux           ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Root-Prüfung
if [[ $EUID -ne 0 ]]; then
    log_error "Dieses Skript muss als root ausgeführt werden!"
    exit 1
fi

# ============ 1. System-Pakete installieren ============
log_info "Aktualisiere Paketlisten..."
apt-get update -qq

log_info "Installiere benötigte Pakete..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    sudo \
    rsync \
    python3 \
    python3-pip \
    python3-venv \
    postgresql \
    postgresql-client \
    nginx \
    curl \
    git \
    openssl \
    ca-certificates \
    2>&1 | tail -5

log_ok "Pakete installiert"

# ============ 2. PostgreSQL einrichten ============
log_info "Starte PostgreSQL..."
systemctl enable postgresql --quiet
systemctl start postgresql

# DB-Passwort aus Quell-.env übernehmen falls vorhanden, sonst zufällig generieren
DB_NAME="evepi"
DB_USER="evepi"
_src_env="${PROJECT_DIR}/.env"
DB_PASSWORD=""
if [[ -f "${_src_env}" ]]; then
    _dbpw=$(grep "^DB_PASSWORD=" "${_src_env}" | cut -d= -f2- | tr -d '[:space:]')
    if [[ -n "$_dbpw" && "$_dbpw" != "sicheres_passwort" && "$_dbpw" != "PASSWORT" ]]; then
        DB_PASSWORD="$_dbpw"
        log_ok "DB-Passwort aus .env übernommen"
    fi
fi
if [[ -z "$DB_PASSWORD" ]]; then
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=\n' | head -c 32)
    log_info "Zufälliges DB-Passwort generiert"
fi

log_info "Erstelle PostgreSQL Benutzer und Datenbank..."
sudo -u postgres psql -c "DROP DATABASE IF EXISTS ${DB_NAME};" 2>/dev/null || true
sudo -u postgres psql -c "DROP USER IF EXISTS ${DB_USER};" 2>/dev/null || true
sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"

log_ok "Datenbank '${DB_NAME}' erstellt"

# ============ 3. System-Benutzer erstellen ============
log_info "Erstelle System-Benutzer '${APP_USER}'..."
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --home "${APP_DIR}" --shell /bin/bash "${APP_USER}"
    log_ok "Benutzer '${APP_USER}' erstellt"
else
    log_warn "Benutzer '${APP_USER}' existiert bereits"
fi

# ============ 4. App-Verzeichnis einrichten ============
log_info "Richte App-Verzeichnis ein: ${APP_DIR}"
mkdir -p "${APP_DIR}"

# Falls Skript aus dem Projekt-Verzeichnis ausgeführt wird
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

if [[ -f "${PROJECT_DIR}/requirements.txt" ]]; then
    log_info "Kopiere App-Dateien nach ${APP_DIR}..."
    cp -r "${PROJECT_DIR}/." "${APP_DIR}/"
    log_ok "App-Dateien kopiert"
else
    log_warn "Keine App-Dateien gefunden. Bitte App manuell nach ${APP_DIR} kopieren."
fi

# ============ 5. Python Virtual Environment ============
log_info "Erstelle Python Virtual Environment..."
python3 -m venv "${APP_DIR}/venv"

log_info "Installiere Python-Abhängigkeiten..."
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
log_ok "Python-Umgebung eingerichtet"

# ============ 6. .env Datei generieren ============
SECRET_KEY=$(openssl rand -base64 48 | tr -d '/+=\n' | head -c 48)

# Vorhandene EVE-Credentials aus Quell-.env übernehmen (falls ausgefüllt)
EVE_CLIENT_ID_VAL="BITTE_AUSFULLEN"
EVE_CLIENT_SECRET_VAL="BITTE_AUSFULLEN"
EVE_CALLBACK_URL_VAL="http://$(hostname -I | awk '{print $1}')/auth/callback"
EVE_SCOPES_VAL="esi-planets.manage_planets.v1,esi-planets.read_customs_offices.v1,esi-location.read_location.v1,esi-search.search_structures.v1"

SOURCE_ENV="${PROJECT_DIR}/.env"
if [[ -f "${SOURCE_ENV}" ]]; then
    _id=$(grep     "^EVE_CLIENT_ID="     "${SOURCE_ENV}" | cut -d= -f2- | tr -d '[:space:]')
    _sec=$(grep    "^EVE_CLIENT_SECRET=" "${SOURCE_ENV}" | cut -d= -f2- | tr -d '[:space:]')
    _cb=$(grep     "^EVE_CALLBACK_URL="  "${SOURCE_ENV}" | cut -d= -f2- | tr -d '[:space:]')
    _sk=$(grep     "^SECRET_KEY="        "${SOURCE_ENV}" | cut -d= -f2- | tr -d '[:space:]')
    _sc=$(grep     "^EVE_SCOPES="        "${SOURCE_ENV}" | cut -d= -f2- | tr -d '[:space:]')
    [[ -n "$_id"  && "$_id"  != "BITTE_AUSFULLEN" ]] && EVE_CLIENT_ID_VAL="$_id"
    [[ -n "$_sec" && "$_sec" != "BITTE_AUSFULLEN" ]] && EVE_CLIENT_SECRET_VAL="$_sec"
    [[ -n "$_cb"  && "$_cb"  != "BITTE_AUSFULLEN" ]] && EVE_CALLBACK_URL_VAL="$_cb"
    [[ -n "$_sk"  && ${#_sk} -ge 32               ]] && SECRET_KEY="$_sk"
    [[ -n "$_sc"                                   ]] && EVE_SCOPES_VAL="$_sc"
fi

if [[ "$EVE_CLIENT_ID_VAL" != "BITTE_AUSFULLEN" ]]; then
    log_ok "EVE-Credentials aus bestehender .env übernommen"
else
    log_warn "Keine EVE-Credentials gefunden – bitte ${APP_DIR}/.env nach dem Setup ausfüllen"
fi

log_info "Generiere .env Datei..."
cat > "${APP_DIR}/.env" << EOF
# EVE PI Manager Konfiguration
# Generiert am: $(date '+%Y-%m-%d %H:%M:%S')

# Datenbank
DATABASE_URL=postgresql://${DB_USER}:${DB_PASSWORD}@localhost/${DB_NAME}

# Docker: Passwort für den PostgreSQL-Container
DB_PASSWORD=${DB_PASSWORD}

# EVE Online SSO – https://developers.eveonline.com
EVE_CLIENT_ID=${EVE_CLIENT_ID_VAL}
EVE_CLIENT_SECRET=${EVE_CLIENT_SECRET_VAL}
EVE_CALLBACK_URL=${EVE_CALLBACK_URL_VAL}

# ESI Scopes
EVE_SCOPES=${EVE_SCOPES_VAL}

# Sicherheit
SECRET_KEY=${SECRET_KEY}

# Server
APP_PORT=${APP_PORT}
DEBUG=false
EOF

chmod 600 "${APP_DIR}/.env"
log_ok ".env erstellt"

# ============ 7. Dateirechte setzen ============
log_info "Setze Dateirechte..."
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
find "${APP_DIR}" -type d -exec chmod 755 {} \;   # Alle Verzeichnisse traversierbar (Nginx)
find "${APP_DIR}" -type f -exec chmod 644 {} \;   # Alle Dateien lesbar
chmod 755 "${APP_DIR}/venv/bin/"* 2>/dev/null || true  # Executables wieder ausführbar
chmod 600 "${APP_DIR}/.env"                       # .env nur für Owner lesbar

# ============ 8. Alembic Migrationen ============
log_info "Führe Datenbankmigrationen aus..."
cd "${APP_DIR}"
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/alembic" upgrade head
log_ok "Migrationen abgeschlossen"

# ============ 9. Systemd Service ============
log_info "Erstelle systemd Service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=EVE PI Manager - Planetary Industry Dashboard
After=postgresql.service network.target
Wants=postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${APP_PORT} --workers 1
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
# Sicherheitshärtung
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" --quiet
systemctl start "${SERVICE_NAME}"
log_ok "Systemd Service gestartet"

# ============ 10. Nginx konfigurieren ============
log_info "Konfiguriere Nginx..."
cat > "/etc/nginx/sites-available/${SERVICE_NAME}" << 'NGINX_EOF'
server {
    listen 80;
    server_name _;

    # Static Files direkt von Nginx servieren
    location /static {
        alias /opt/eve-pi-manager/app/static;
        expires 7d;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # Proxy zu FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
        proxy_buffering off;
        client_max_body_size 1M;
    }

    # Logging
    access_log /var/log/nginx/eve-pi-manager-access.log;
    error_log /var/log/nginx/eve-pi-manager-error.log;
}
NGINX_EOF

# Default site deaktivieren, eigene aktivieren
rm -f /etc/nginx/sites-enabled/default
ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"

nginx -t && systemctl enable nginx --quiet && systemctl restart nginx
log_ok "Nginx konfiguriert und gestartet"

# ============ 11. Status prüfen ============
sleep 2
APP_STATUS=$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || echo "failed")
NGINX_STATUS=$(systemctl is-active nginx 2>/dev/null || echo "failed")
PG_STATUS=$(systemctl is-active postgresql 2>/dev/null || echo "failed")

# ============ Zusammenfassung ============
IP_ADDR=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Installation abgeschlossen         ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Services:${NC}"
echo -e "  PostgreSQL: $([ "$PG_STATUS" = "active" ] && echo -e "${GREEN}aktiv${NC}" || echo -e "${RED}${PG_STATUS}${NC}")"
echo -e "  App:        $([ "$APP_STATUS" = "active" ] && echo -e "${GREEN}aktiv${NC}" || echo -e "${RED}${APP_STATUS}${NC}")"
echo -e "  Nginx:      $([ "$NGINX_STATUS" = "active" ] && echo -e "${GREEN}aktiv${NC}" || echo -e "${RED}${NGINX_STATUS}${NC}")"
echo ""
echo -e "  ${CYAN}URL:${NC}        http://${IP_ADDR}"
echo -e "  ${CYAN}DB Passwort:${NC} ${DB_PASSWORD}"
echo ""
echo -e "  ${YELLOW}⚠  Nächste Schritte:${NC}"
echo -e "  1. EVE App registrieren: https://developers.eveonline.com"
echo -e "  2. .env bearbeiten: nano ${APP_DIR}/.env"
echo -e "     EVE_CLIENT_ID und EVE_CLIENT_SECRET eintragen"
echo -e "     EVE_CALLBACK_URL auf http://${IP_ADDR}/auth/callback setzen"
echo -e "  3. Service neu starten: systemctl restart ${SERVICE_NAME}"
echo -e "  4. Browser öffnen: http://${IP_ADDR}"
echo -e "  5. Erster Login = Admin"
echo ""
echo -e "  ${CYAN}Logs anzeigen:${NC}  journalctl -u ${SERVICE_NAME} -f"
echo -e "  ${CYAN}Installation:${NC}   ${APP_DIR}"
echo ""
