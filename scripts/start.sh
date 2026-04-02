#!/usr/bin/env bash
# ============================================================
# PlanetFlow — Start Script
#
# Führt alle Schritte aus die beim ersten Start nötig sind:
#   1. .env prüfen
#   2. TLS-Zertifikat holen (einmalig, wenn noch nicht vorhanden)
#   3. Docker Stack starten
#
# Verwendung (als root im Repository-Verzeichnis):
#   bash scripts/start.sh
# ============================================================
set -euo pipefail

DOMAIN="planetflow.app"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}    $1"; }
info() { echo -e "${CYAN}[INFO]${NC}  $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
die()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

cd "$APP_DIR"

echo ""
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo -e "${CYAN}        PlanetFlow — Start                ${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

# ── 1. .env prüfen ───────────────────────────────────────────
if [[ ! -f "$APP_DIR/.env" ]]; then
    die ".env nicht gefunden. Bitte zuerst ausführen: cp .env.example .env && nano .env"
fi

# Pflichtfelder prüfen
MISSING=()
for VAR in DB_PASSWORD SECRET_KEY EVE_CLIENT_ID EVE_CLIENT_SECRET EVE_CALLBACK_URL RABBITMQ_PASS; do
    VAL=$(grep "^${VAR}=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
    if [[ -z "$VAL" || "$VAL" == "change_me"* || "$VAL" == "replace_me"* || "$VAL" == "your_"* ]]; then
        MISSING+=("$VAR")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo -e "${RED}[ERROR]${NC} Folgende Pflichtfelder in .env sind nicht gesetzt:"
    for V in "${MISSING[@]}"; do echo "          - $V"; done
    echo ""
    echo "  Bearbeite die Datei: nano $APP_DIR/.env"
    exit 1
fi
ok ".env ist vollständig"

# ── 2. Docker prüfen ─────────────────────────────────────────
command -v docker &>/dev/null || die "Docker nicht gefunden. Bitte Docker installieren."
docker compose version &>/dev/null || die "Docker Compose Plugin nicht gefunden."
ok "Docker verfügbar"

# ── 3. TLS-Zertifikat ────────────────────────────────────────
CERT_PATH="/var/lib/docker/volumes/$(basename "$APP_DIR")_certbot_conf/_data/live/$DOMAIN/fullchain.pem"
# Prüfe alternativ via docker volume inspect
CERT_EXISTS=false
if docker volume inspect "$(basename "$APP_DIR")_certbot_conf" &>/dev/null 2>&1; then
    if docker run --rm \
        -v "$(basename "$APP_DIR")_certbot_conf:/etc/letsencrypt" \
        alpine:3 \
        test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" 2>/dev/null; then
        CERT_EXISTS=true
    fi
fi

if $CERT_EXISTS; then
    ok "TLS-Zertifikat vorhanden"
else
    info "Kein TLS-Zertifikat gefunden — hole Let's Encrypt Zertifikat..."
    echo ""
    warn "Port 80 muss frei sein (kein laufender nginx/Apache)."
    echo ""

    # Volumes anlegen damit certbot sie befüllen kann
    docker volume create "$(basename "$APP_DIR")_certbot_conf" >/dev/null 2>&1 || true
    docker volume create "$(basename "$APP_DIR")_certbot_www"  >/dev/null 2>&1 || true

    ADMIN_EMAIL=$(grep "^EVE_CLIENT_ID=" "$APP_DIR/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]' | xargs -I{} echo "admin@$DOMAIN" || echo "admin@$DOMAIN")

    docker run --rm \
        -v "$(basename "$APP_DIR")_certbot_conf:/etc/letsencrypt" \
        -v "$(basename "$APP_DIR")_certbot_www:/var/www/certbot" \
        -p 80:80 \
        certbot/certbot certonly \
        --standalone \
        --email "admin@${DOMAIN}" \
        --agree-tos \
        --no-eff-email \
        -d "$DOMAIN" \
        -d "www.$DOMAIN" \
        || die "Zertifikat konnte nicht ausgestellt werden. Prüfe ob DNS für $DOMAIN auf diesen Server zeigt."

    ok "TLS-Zertifikat erfolgreich ausgestellt"
fi

# ── 4. Stack starten ─────────────────────────────────────────
info "Starte Docker Stack..."
docker compose up -d --build

echo ""
ok "Stack gestartet. Warte auf App..."
sleep 5

# Health-Check
MAX_WAIT=60
WAITED=0
until docker compose exec -T app curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        warn "App antwortet noch nicht nach ${MAX_WAIT}s — prüfe Logs:"
        echo "  docker compose logs app"
        break
    fi
    sleep 3
    WAITED=$((WAITED + 3))
done

if docker compose exec -T app curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    ok "App ist erreichbar"
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  PlanetFlow läuft: https://$DOMAIN      ${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo "  Logs:    docker compose logs -f"
echo "  Status:  docker compose ps"
echo "  Update:  bash scripts/update.sh"
echo ""
