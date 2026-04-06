#!/usr/bin/env bash
# ============================================================
# PlanetFlow - Start Script
#
# 1. Validate .env
# 2. In https mode: obtain TLS certificate if missing
# 3. Start Docker stack
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
echo -e "${CYAN}==========================================${NC}"
echo -e "${CYAN}        PlanetFlow - Start               ${NC}"
echo -e "${CYAN}==========================================${NC}"
echo ""

if [[ ! -f "$APP_DIR/.env" ]]; then
    die ".env not found. Run: cp .env.example .env && edit .env"
fi

# Validate required values
MISSING=()
for VAR in DB_PASSWORD SECRET_KEY EVE_CLIENT_ID EVE_CLIENT_SECRET EVE_CALLBACK_URL RABBITMQ_PASS; do
    VAL=$(grep "^${VAR}=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
    if [[ -z "$VAL" || "$VAL" == "change_me"* || "$VAL" == "replace_me"* || "$VAL" == "your_"* ]]; then
        MISSING+=("$VAR")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo -e "${RED}[ERROR]${NC} Missing required .env fields:"
    for V in "${MISSING[@]}"; do echo "  - $V"; done
    echo ""
    die "Please edit $APP_DIR/.env"
fi
ok ".env is complete"

command -v docker &>/dev/null || die "Docker not found"
docker compose version &>/dev/null || die "Docker Compose plugin not found"
ok "Docker available"

NGINX_MODE_VAL=$(grep "^NGINX_MODE=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
[[ -n "$NGINX_MODE_VAL" ]] || NGINX_MODE_VAL="https"
HTTP_PORT=$(grep "^NGINX_HTTP_PORT=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
[[ -n "$HTTP_PORT" ]] || HTTP_PORT="80"

if [[ "$NGINX_MODE_VAL" == "https" ]]; then
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
        ok "TLS certificate already present"
    else
        info "No TLS certificate found - requesting Let's Encrypt certificate..."
        warn "Port 80 must be free for certbot standalone challenge."

        docker volume create "$(basename "$APP_DIR")_certbot_conf" >/dev/null 2>&1 || true
        docker volume create "$(basename "$APP_DIR")_certbot_www"  >/dev/null 2>&1 || true

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
            || die "Could not issue certificate. Check DNS for $DOMAIN."

        ok "TLS certificate issued"
    fi
else
    info "NGINX_MODE=$NGINX_MODE_VAL - skipping TLS certificate setup."
fi

info "Starting Docker stack..."
docker compose up -d --build

echo ""
ok "Stack started. Waiting for app..."
sleep 5

MAX_WAIT=60
WAITED=0
until docker compose exec -T app curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        warn "App not reachable after ${MAX_WAIT}s. Check: docker compose logs app"
        break
    fi
    sleep 3
    WAITED=$((WAITED + 3))
done

if docker compose exec -T app curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    ok "App is reachable"
fi

echo ""
echo -e "${GREEN}==========================================${NC}"
if [[ "$NGINX_MODE_VAL" == "https" ]]; then
    echo -e "${GREEN}  PlanetFlow is running at: https://$DOMAIN${NC}"
else
    echo -e "${GREEN}  PlanetFlow is running at: http://localhost:$HTTP_PORT${NC}"
fi
echo -e "${GREEN}==========================================${NC}"
echo ""
echo "  Logs:   docker compose logs -f"
echo "  Status: docker compose ps"
echo "  Update: bash scripts/update.sh"
echo ""
