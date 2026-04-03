#!/usr/bin/env bash
# ============================================================
# PlanetFlow — Update Script
#
# Holt den aktuellen Stand von GitHub und startet den Stack neu.
#   1. git pull
#   2. Docker Images neu bauen
#   3. Stack neu starten (zero-downtime soweit möglich)
#   4. Datenbank-Migrationen ausführen
#
# Verwendung (als root im Repository-Verzeichnis):
#   bash scripts/update.sh
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="main"

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
echo -e "${CYAN}        PlanetFlow — Update               ${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

# ── Voraussetzungen ──────────────────────────────────────────
command -v docker &>/dev/null  || die "Docker nicht gefunden."
docker compose version &>/dev/null || die "Docker Compose Plugin nicht gefunden."
[[ -d "$APP_DIR/.git" ]]       || die "$APP_DIR ist kein Git-Repository."
[[ -f "$APP_DIR/.env" ]]       || die ".env nicht gefunden — kopiere .env.example und fülle alle Werte aus."

# ── .env Vollständigkeitsprüfung ─────────────────────────────
if [[ -f "$APP_DIR/.env.example" ]]; then
    info "Prüfe .env auf fehlende Einträge..."
    missing=()
    while IFS= read -r line; do
        # Skip blank lines, comments, and lines that don't contain '='
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        key="${line%%=*}"
        [[ -z "$key" ]] && continue
        # Check if key exists in .env (as KEY= at start of a non-commented line)
        if ! grep -qE "^[[:space:]]*${key}[[:space:]]*=" "$APP_DIR/.env" 2>/dev/null; then
            missing+=("$key")
        fi
    done < "$APP_DIR/.env.example"

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo ""
        echo -e "${RED}══════════════════════════════════════════${NC}"
        echo -e "${RED}  .env unvollständig — Update abgebrochen ${NC}"
        echo -e "${RED}══════════════════════════════════════════${NC}"
        echo ""
        echo -e "${RED}Folgende Einträge fehlen in .env:${NC}"
        for key in "${missing[@]}"; do
            echo -e "  ${YELLOW}→ ${key}${NC}"
        done
        echo ""
        echo "  Füge die fehlenden Einträge zu .env hinzu."
        echo "  Vorlage: $APP_DIR/.env.example"
        echo ""
        exit 1
    fi
    ok ".env vollständig"
fi

# ── 1. Git pull ──────────────────────────────────────────────
info "Hole aktuelle Version von GitHub (origin/$BRANCH)..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
ok "Code aktualisiert auf $(git log --oneline -1)"

# ── 2. Images neu bauen ──────────────────────────────────────
info "Baue Docker Images..."
docker compose build --pull
ok "Images gebaut"

# ── 3. Stack neu starten ─────────────────────────────────────
info "Starte Stack neu..."
docker compose up -d
ok "Stack neu gestartet"

# ── 4. Datenbank-Migrationen ─────────────────────────────────
info "Führe Datenbank-Migrationen aus..."
# Warte bis App-Container healthy ist
MAX_WAIT=60
WAITED=0
until docker compose exec -T app alembic upgrade head 2>/dev/null; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        die "App-Container nicht bereit nach ${MAX_WAIT}s. Prüfe: docker compose logs app"
    fi
    sleep 3
    WAITED=$((WAITED + 3))
done
ok "Migrationen abgeschlossen"

# ── Status ───────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}        Update abgeschlossen              ${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
docker compose ps --format "table {{.Name}}\t{{.Status}}"
echo ""
echo "  Logs: docker compose logs -f"
echo ""
