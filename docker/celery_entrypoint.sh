#!/bin/sh
set -e

echo "Warte auf RabbitMQ..."
until python -c "
import os, sys, time
url = os.environ.get('CELERY_BROKER_URL', 'amqp://guest:guest@rabbitmq:5672//')
ok = False
for i in range(30):
    try:
        import kombu
        conn = kombu.Connection(url)
        conn.ensure_connection(max_retries=1, timeout=3)
        conn.close()
        ok = True
        break
    except Exception:
        time.sleep(2)
sys.exit(0 if ok else 1)
" 2>/dev/null; do
    sleep 2
done

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

echo "Starte Celery..."
exec "$@"
