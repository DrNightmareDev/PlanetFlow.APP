#!/usr/bin/env bash
# ============================================================
# PlanetFlow — Initial Let's Encrypt Certificate Setup
#
# Run this ONCE on a fresh server after docker compose up -d.
# nginx will be in HTTP-only bootstrap mode until this completes.
#
# Usage:
#   bash scripts/init-cert.sh your@email.com
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}    $1"; }
info() { echo -e "${CYAN}[INFO]${NC}  $1"; }
die()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

EMAIL="${1:-}"
[[ -n "$EMAIL" ]] || die "Usage: bash scripts/init-cert.sh your@email.com"

cd "$APP_DIR"

# Load domain from .env if present
DOMAIN="${DOMAIN:-planetflow.app}"

info "Requesting Let's Encrypt certificate for $DOMAIN and www.$DOMAIN ..."
info "Make sure nginx is running and port 80 is reachable from the internet."
echo ""

docker compose run --rm certbot certonly \
    --webroot \
    -w /var/www/certbot \
    -d "$DOMAIN" \
    -d "www.$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --non-interactive

ok "Certificate issued."
echo ""
info "nginx will detect the cert within 5 seconds and reload automatically."
info "You can also force-reload with: docker compose restart nginx"
echo ""
