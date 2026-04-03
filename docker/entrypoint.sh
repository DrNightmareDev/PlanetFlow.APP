#!/bin/sh
set -e

# ── Env-Variablen Vollständigkeitsprüfung ────────────────────
# Liest alle Schlüssel aus .env.example und prüft ob sie als
# Umgebungsvariablen gesetzt sind (Docker setzt sie via env_file).
ENV_EXAMPLE="/app/.env.example"
if [ -f "$ENV_EXAMPLE" ]; then
    missing=""
    while IFS= read -r line; do
        # Skip blank lines and comments
        case "$line" in
            ""|"#"*) continue ;;
        esac
        # Must contain '='
        case "$line" in
            *"="*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        # Strip leading whitespace from key
        key="$(echo "$key" | sed 's/^[[:space:]]*//')"
        [ -z "$key" ] && continue
        # Use printenv to check if the variable is set in the environment
        if ! printenv "$key" > /dev/null 2>&1; then
            missing="${missing}  → ${key}\n"
        fi
    done < "$ENV_EXAMPLE"

    if [ -n "$missing" ]; then
        echo ""
        echo "════════════════════════════════════════"
        echo "  FEHLER: Umgebungsvariablen fehlen"
        echo "════════════════════════════════════════"
        echo ""
        echo "Folgende Variablen aus .env.example sind nicht gesetzt:"
        printf "%b" "$missing"
        echo ""
        echo "Prüfe die .env Datei auf dem Host."
        echo ""
        exit 1
    fi
    echo ".env Prüfung OK"
fi

echo "Warte auf Datenbank..."
until python -c "
import os, re, sys, time
import psycopg2
url = os.environ.get('DATABASE_URL', '')
m = re.match(r'postgresql://([^:]+):([^@]+)@([^/]+)/(.+)', url)
ok = False
if m:
    user, pw, host, db = m.groups()
    for i in range(30):
        try:
            conn = psycopg2.connect(host=host, user=user, password=pw, dbname=db)
            conn.close()
            ok = True
            break
        except Exception:
            time.sleep(1)
sys.exit(0 if ok else 1)
" 2>/dev/null; do
    sleep 1
done

echo "Datenbank erreichbar. Starte Migrationen..."
alembic upgrade head

echo "Starte Anwendung..."
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    --workers "${WEB_WORKERS:-4}" \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
