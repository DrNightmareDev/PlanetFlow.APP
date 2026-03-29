# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

Planetary Industry Dashboard fuer EVE Online - selbst gehostet.

Wenn dir das Projekt hilft, freue ich mich ueber Ingame-ISK-Spenden an `DrNightmare`.

## Features

- PI Kolonien fuer Main und Alts ueber beliebig viele Accounts und Charaktere
- **Celery + RabbitMQ Hintergrund-Refresh** — ESI-Daten werden im Hintergrund alle 30 Minuten aktualisiert; das Dashboard laedt immer sofort aus dem Cache
- **ETag-basiertes ESI-Caching** — unveraenderte Planeten geben HTTP 304 zurueck und werden nicht neu verarbeitet (~60–70% weniger ESI-Anfragen nach dem ersten Durchlauf)
- Persistente DB-Caches fuer Marktpreise, Dashboard-Daten, Skyhook-Werte, ETag-Antworten, GUI-Uebersetzungen und statische Planetendetails
- Automatischer 15-Minuten-Marktpreis-Refresh und 30-Minuten-Kolonie-Refresh via Celery Beat
- Dashboard mit Statusfiltern, ISK/Tag, Ablaufwarnungen, Extraktor-Balance-Indikatoren, Extraktor-Ratenfiltern, Tier-Filter, Auto-Refresh-Countdown und Dotlan-Links
- **PI Templates** — Kolonie-Layouts speichern, teilen und importieren mit massstabsgetreuem Canvas-Rendering und Community-Templates von GitHub
- Skyhook Inventar mit Verlauf und DB-Wert-Cache
- PI Skills pro Charakter in Karten- und Listenansicht
- Corporation-Uebersicht, System Analyzer, Vergleich, System Mix und PI Chain Planner
- Manager Panel und DB-basierte GUI-Uebersetzungen fuer Deutsch, Englisch und vereinfachtes Chinesisch
- Optionales **Sentry** Error-Tracking und **Flower** Celery-Task-Monitor

## Seiten im UI

- `Dashboard`: Zeigt alle PI-Kolonien, den taeglichen ISK-Wert, Ablaufzeiten, Storage-Staende, Skyhook-Bezug, Auto-Refresh-Countdown und Filter fuer Aktiv, Abgelaufen, Stalled, Balanced, Unbalanced und Extraktor-Raten.
- `PI Templates`: Canvas-Editor fuer Kolonie-Layouts mit massstabsgetreuem Planeten-Rendering und Import aus Community-GitHub-Quellen (DalShooth, TheLegi0n-NBI).
- `Skyhooks`: Bearbeiten und Speichern von Skyhook-Bestaenden pro Planet inklusive Verlaufsansicht und Wertberechnung.
- `Characters`: Zeigt alle verknuepften Charaktere, Main/Alt-Zuordnung, Token-Status und PI-Skills in Karten- und Listenansicht.
- `Corporation`: Fasst die PI-Daten der eigenen Corporation zusammen, zeigt Main-Charaktere, Kolonien, PI-Typen und Produktsuche ueber alle Corp-Kolonien.
- `Jita Markt`: Zeigt Kauf-/Verkaufspreise, Spreads, Trends und Handelsvolumen fuer PI-Produkte aus dem Jita-/The-Forge-Marktcache.
- `PI Chain Planner`: Baut Produktionsketten fuer P1-P4 auf, zeigt benoetigte Planetentypen, P0-Rohstoffe und passende Systeme.
- `System Analyzer`: Analysiert ein einzelnes System, zeigt verfuegbare Planetentypen, P0-Ressourcen, daraus abgeleitete PI-Empfehlungen sowie ausklappbare Planetendetails mit Planetennummer und Radius.
- `System Mix`: Kombiniert mehrere Systeme oder Konstellationen und zeigt, welche PI-Produkte mit den gemeinsamen Planetentypen moeglich sind.
- `Vergleich`: Vergleicht mehrere Systeme direkt nebeneinander, inklusive Planetentypen und PI-Empfehlungen.

## Benoetigte ESI-Scopes

```
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-search.search_structures.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
```

## Schnellstart

```bash
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager
cp .env.example .env
```

Danach `.env` ausfuellen und je nach Zielsystem Docker Compose, Linux oder Windows verwenden.

## `.env` konfigurieren

### Pflichtwerte

```env
DATABASE_URL=postgresql://evepi:PASSWORT@localhost/evepi
EVE_CLIENT_ID=dein_client_id
EVE_CLIENT_SECRET=dein_client_secret
EVE_CALLBACK_URL=http://DEINE-IP-ODER-DOMAIN/auth/callback
SECRET_KEY=min_32_zeichen_langer_zufaelliger_schluessel
```

- `EVE_CLIENT_ID` / `EVE_CLIENT_SECRET`: aus dem [CCP Developer Portal](https://developers.eveonline.com)
- `EVE_CALLBACK_URL`: muss exakt zur Redirect-URL der CCP-App passen
- `SECRET_KEY`: fuer Session-Signierung — unbedingt durch eigenen sicheren Wert ersetzen

### RabbitMQ / Celery (fuer Hintergrund-Refresh erforderlich)

```env
RABBITMQ_USER=evepi
RABBITMQ_PASS=change_me_rabbit
CELERY_BROKER_URL=amqp://evepi:change_me_rabbit@rabbitmq:5672//
```

- `@rabbitmq:5672` fuer Docker Compose, `@localhost:5672` fuer native Linux-Installation.
- `CELERY_BROKER_URL` leer lassen, um ohne Celery zu betreiben (APScheduler-Fallback, Single-Process — nicht empfohlen fuer grosse Corps).

### Performance

```env
# Jeder gunicorn-Worker laedt die gesamte App (~400-500 MB).
# Mit 2 GB RAM 2 Worker, mit 4+ GB RAM 2-4 Worker.
WEB_WORKERS=2
```

### Optionale Integrationen

```env
JANICE_API_KEY=

# Sentry Error-Tracking — leer lassen zum Deaktivieren
SENTRY_DSN=

# Flower Celery-Monitor-Zugangsdaten (bei --profile monitoring)
FLOWER_USER=admin
FLOWER_PASS=change_me_flower

# nginx-Profil-Port (bei --profile nginx)
NGINX_PORT=80
```

### Vollstaendiges Beispiel

```env
DATABASE_URL=postgresql://evepi:supersecret@localhost/evepi
DB_PASSWORD=supersecret
EVE_CLIENT_ID=1234567890abcdef
EVE_CLIENT_SECRET=abcdef1234567890
EVE_CALLBACK_URL=http://192.168.2.44/auth/callback
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
SECRET_KEY=replace_me_with_a_long_random_secret_key
APP_PORT=8000
DEBUG=false
WEB_WORKERS=2
RABBITMQ_USER=evepi
RABBITMQ_PASS=supersecret_rabbit
CELERY_BROKER_URL=amqp://evepi:supersecret_rabbit@localhost:5672//
SENTRY_DSN=
```

## Docker Compose

### Starten

```bash
docker compose up -d
```

Startet den Core-Stack: **PostgreSQL**, **RabbitMQ**, die **Web-App** (gunicorn), **Celery Worker** und **Celery Beat** Scheduler.

### Optionale Profile

| Profil | Befehl | Zweck |
|---|---|---|
| `nginx` | `--profile nginx` | Integrierter nginx Reverse Proxy (weglassen wenn eigener nginx vorhanden) |
| `pgbouncer` | `--profile pgbouncer` | PgBouncer Connection Pooler fuer sehr grosse Deployments |
| `monitoring` | `--profile monitoring` | Flower Celery-Task-Monitor (localhost:5555) |

Beispiel mit nginx:
```bash
docker compose --profile nginx up -d
```

### Update

```bash
bash scripts/update_linux.sh --compose
```

Oder manuell:
```bash
git pull origin main
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

### Logs

```bash
docker compose logs -f app
docker compose logs -f celery_worker
docker compose logs -f celery_beat
```

### Administrator-Skripte

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Charaktername"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789
docker compose exec app python /app/scripts/remove_administrator.py --name "Charaktername"
docker compose exec app python /app/scripts/remove_administrator.py --eve-id 123456789
```

## Linux (nativ)

### Neuinstallation

```bash
sudo bash scripts/setup_linux.sh
```

Installiert und konfiguriert: PostgreSQL, RabbitMQ, Python venv, Alembic-Migrationen und drei systemd-Services:

| Service | Beschreibung |
|---|---|
| `eve-pi-manager` | Web-App (gunicorn) |
| `eve-pi-manager-worker` | Celery Worker (ESI-Hintergrund-Refresh) |
| `eve-pi-manager-beat` | Celery Beat Scheduler (triggert Refresh alle 30 Min.) |

### Upgrade von einer alten Version

```bash
sudo bash scripts/upgrade_to_latest.sh
```

Erledigt automatisch:
- RabbitMQ installieren falls fehlend
- Fehlende `.env`-Keys ergaenzen ohne bestehende Werte zu aendern
- Web-Service von `uvicorn` → `gunicorn` upgraden falls noetig
- Celery Worker und Beat als systemd-Units anlegen
- `pip install` und `alembic upgrade head` ausfuehren
- Alle Services neu starten

### Regulaeres Update

```bash
sudo bash scripts/update_linux.sh
```

### Service-Status pruefen

```bash
systemctl status eve-pi-manager eve-pi-manager-worker eve-pi-manager-beat
```

### Logs

```bash
journalctl -u eve-pi-manager -f
journalctl -u eve-pi-manager-worker -f
journalctl -u eve-pi-manager-beat -f
```

### RAM-Orientierung

| Server RAM | WEB_WORKERS |
|---|---|
| 1 GB | 1 |
| 2 GB | 2 |
| 4 GB+ | 2–4 |

## Windows nativ

Voraussetzungen:
- Python 3.11+
- PostgreSQL lokal oder extern
- Ausgefuellte `.env`

Hinweis: RabbitMQ und Celery werden vom Windows-Skript nicht eingerichtet. Die App faellt auf APScheduler zurueck (Single-Process, kein Hintergrund-Worker). Fuer Produktion mit vielen Charakteren wird Linux oder Docker empfohlen.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Update:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

## Health-Check

```
GET /health
```

Gibt den Status von PostgreSQL und RabbitMQ zurueck:

```json
{
  "status": "ok",
  "database": "ok",
  "rabbitmq": "ok"
}
```

## Architektur

```
Browser
  └─► nginx (optional) ──► gunicorn (2–4 Worker)
                                └─► FastAPI / Jinja2
                                └─► PostgreSQL (SQLAlchemy)

RabbitMQ ──► Celery Worker (4 Concurrency)
                └─► ESI API (ETag-gecacht, 304-aware)
                └─► DashboardCache-Tabelle (PostgreSQL)

Celery Beat ──► auto_refresh_stale_accounts  (alle 5 Min.)
            └─► refresh_market_prices_task   (alle 15 Min.)
            └─► cleanup_sso_states_task      (alle 1 Stunde)
```

**Dashboard-Ladeablauf:**
1. Request trifft gunicorn — liest `DashboardCache` aus DB (schnell, kein ESI)
2. Cache fehlt oder veraltet: Celery-Task dispatchen, Lade-Spinner anzeigen
3. JS pollt `/dashboard/refresh-status?since=<timestamp>` alle 3 Sekunden
4. Wenn Celery Worker den Cache aktualisiert hat, erkennt das Polling es und laedt neu
5. Folge-Requests werden sofort aus Cache bedient

## Administrator-Skripte

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Charaktername"
./scripts/add_administrator.py --eve-id 123456789
./scripts/remove_administrator.py --name "Charaktername"
./scripts/remove_administrator.py --eve-id 123456789
```

- `add_administrator.py` setzt den Account auf `Administrator` und `Manager`
- `remove_administrator.py` entfernt diese Rollen

## Uebersetzungen

- GUI-Uebersetzungen werden aus `translation_entries` in der Datenbank geladen
- Seed-Dateien unter `app/locales/` liefern den Bootstrap-Bestand
- Offizielle PI-Produktnamen werden aus der EVE SDE (`types.json`) importiert
- Statische Planetendetails (Planetennummer, Radius) kommen aus SDE-Universe-Daten
- SDE-Eintraege wie `type.<id>.name` sind im Manager schreibgeschuetzt

## Tech Stack

| Komponente | Technologie |
|---|---|
| Web-Framework | FastAPI + Jinja2 |
| Datenbank | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| Hintergrund-Jobs | Celery 5 + RabbitMQ |
| Web-Server | gunicorn + UvicornWorker |
| Frontend | Bootstrap 5 |
| ESI-Caching | ETag / If-None-Match (HTTP 304) |
| Dev-Fallback | APScheduler (kein RabbitMQ noetig) |
| Error-Tracking | Sentry SDK (optional) |
| Task-Monitor | Flower (optional, `--profile monitoring`) |

## CCP-Hinweis

EVE Online und alle zugehoerigen Logos und Designs sind Marken oder eingetragene Marken von CCP ehf. Dieses Projekt steht in keiner Verbindung zu CCP ehf und wird nicht von CCP ehf unterstuetzt oder empfohlen.

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
