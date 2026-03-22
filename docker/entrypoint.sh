#!/bin/sh
set -e

echo "Warte auf Datenbank..."
until python -c "
import psycopg2, os, time
url = os.environ.get('DATABASE_URL', '')
# parse postgresql://user:pass@host/db
import re
m = re.match(r'postgresql://([^:]+):([^@]+)@([^/]+)/(.+)', url)
if m:
    user, pw, host, db = m.groups()
    for i in range(30):
        try:
            psycopg2.connect(host=host, user=user, password=pw, dbname=db)
            break
        except Exception:
            time.sleep(1)
" 2>/dev/null; do
    sleep 1
done

echo "Datenbank erreichbar. Starte Migrationen..."
alembic upgrade head

echo "Starte Anwendung..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
