# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

Self-hosted Planetary Industry dashboard for EVE Online.

If this project helps you, Ingame-ISK donations to `DrNightmare` are welcome.

## Features

- PI colonies for mains and alts across any number of accounts and characters
- **Celery + RabbitMQ background refresh** — ESI data is fetched in the background every 30 minutes; the dashboard always loads instantly from cache
- **ETag-based ESI caching** — unchanged planets return HTTP 304 and skip re-processing (~60–70% fewer ESI calls after the first run)
- Persistent DB caches for market prices, dashboard values, skyhook values, ETag responses, GUI translations, and static planet details
- Automatic 15-minute price refresh and 30-minute colony refresh via Celery Beat
- Dashboard with status filters, ISK/day, expiry timers, extractor balance indicators, extractor-rate filters, tier filtering, auto-refresh countdown, and Dotlan links
- **PI Templates** — save, share, and import colony layouts with to-scale canvas rendering and community templates from GitHub
- Skyhook inventory with history and DB value cache
- PI skills per character in card and list views
- Corporation overview, System Analyzer, Compare, System Mix, and PI Chain Planner
- Manager panel and DB-backed GUI translations for German, English, and Simplified Chinese
- Optional **Sentry** error tracking and **Flower** Celery task monitor

## UI Pages

- `Dashboard`: Shows all PI colonies, daily ISK value, expiry timers, storage state, skyhook context, auto-refresh countdown, and filters for active, expired, stalled, balanced, unbalanced, and extractor-rate thresholds.
- `PI Templates`: Canvas editor for colony layouts with to-scale planet rendering and import from community GitHub sources (DalShooth, TheLegi0n-NBI).
- `Skyhooks`: Edit and save skyhook inventories per planet, including history and cached value calculations.
- `Characters`: Shows all linked characters, main/alt assignments, token state, and PI skills in card and list views.
- `Corporation`: Summarizes PI data for your corporation and shows mains, colonies, PI types, and product search across corporation colonies.
- `Jita Market`: Displays buy/sell prices, spreads, trends, and trade volume for PI products from the cached Jita/The Forge market data.
- `PI Chain Planner`: Builds full production chains for P1-P4 items and shows required planet types, P0 resources, and suitable systems.
- `System Analyzer`: Analyzes a single system and shows available planet types, P0 resources, derived PI recommendations, and expandable planet details with planet number and radius.
- `System Mix`: Combines multiple systems or constellations and shows which PI products are possible with the shared planet mix.
- `Compare`: Places multiple systems side by side to compare planet types and PI recommendations directly.

## Required ESI scopes

```
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-search.search_structures.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
```

## Quick start

```bash
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager
cp .env.example .env
```

Then fill in `.env` and continue with Docker Compose, Linux, or native Windows.

## Configure `.env`

### Required values

```env
DATABASE_URL=postgresql://evepi:PASSWORD@localhost/evepi
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=http://YOUR-IP-OR-DOMAIN/auth/callback
SECRET_KEY=a_long_random_secret_with_at_least_32_characters
```

- `EVE_CLIENT_ID` / `EVE_CLIENT_SECRET`: from the [CCP Developer Portal](https://developers.eveonline.com)
- `EVE_CALLBACK_URL`: must exactly match the redirect URL configured in your CCP app
- `SECRET_KEY`: used for session signing — replace with a random value

### RabbitMQ / Celery (required for background refresh)

```env
RABBITMQ_USER=evepi
RABBITMQ_PASS=change_me_rabbit
CELERY_BROKER_URL=amqp://evepi:change_me_rabbit@rabbitmq:5672//
```

- Use `@rabbitmq:5672` for Docker Compose, `@localhost:5672` for native Linux installs.
- Leave `CELERY_BROKER_URL` empty to run without Celery (APScheduler fallback, single-process dev mode — not recommended for large corps).

### Performance

```env
# Each gunicorn worker loads the full app (~400-500 MB).
# Start with 2, raise to 4 on servers with 4+ GB RAM.
WEB_WORKERS=2
```

### Optional integrations

```env
JANICE_API_KEY=

# Sentry error tracking — leave empty to disable
SENTRY_DSN=

# Flower Celery monitor credentials (used with --profile monitoring)
FLOWER_USER=admin
FLOWER_PASS=change_me_flower

# nginx profile port (used with --profile nginx)
NGINX_PORT=80
```

### Full example

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

### Start

```bash
docker compose up -d
```

This starts the core stack: **PostgreSQL**, **RabbitMQ**, the **web app** (gunicorn), **Celery worker**, and **Celery Beat** scheduler.

### Optional profiles

| Profile | Command | Purpose |
|---|---|---|
| `nginx` | `--profile nginx` | Built-in nginx reverse proxy (skip if you have your own) |
| `pgbouncer` | `--profile pgbouncer` | PgBouncer connection pooler for very large deployments |
| `monitoring` | `--profile monitoring` | Flower Celery task monitor (localhost:5555) |

Example with nginx:
```bash
docker compose --profile nginx up -d
```

### Update

```bash
bash scripts/update_linux.sh --compose
```

Or manually:
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

### Administrator scripts

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789
docker compose exec app python /app/scripts/remove_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/remove_administrator.py --eve-id 123456789
```

## Linux (native)

### Fresh install

```bash
sudo bash scripts/setup_linux.sh
```

Installs and configures: PostgreSQL, RabbitMQ, Python venv, Alembic migrations, and three systemd services:

| Service | Description |
|---|---|
| `eve-pi-manager` | Web app (gunicorn) |
| `eve-pi-manager-worker` | Celery worker (ESI background refresh) |
| `eve-pi-manager-beat` | Celery Beat scheduler (triggers refresh every 30 min) |

### Upgrade from any previous version

```bash
sudo bash scripts/upgrade_to_latest.sh
```

Handles everything automatically:
- Installs RabbitMQ if missing
- Adds new `.env` keys without touching existing values
- Upgrades the web service from `uvicorn` → `gunicorn` if needed
- Creates Celery worker and beat systemd units
- Runs `pip install` and `alembic upgrade head`
- Restarts all services

### Regular update

```bash
sudo bash scripts/update_linux.sh
```

### Check service status

```bash
systemctl status eve-pi-manager eve-pi-manager-worker eve-pi-manager-beat
```

### Logs

```bash
journalctl -u eve-pi-manager -f
journalctl -u eve-pi-manager-worker -f
journalctl -u eve-pi-manager-beat -f
```

### RAM guide

| Server RAM | WEB_WORKERS |
|---|---|
| 1 GB | 1 |
| 2 GB | 2 |
| 4 GB+ | 2–4 |

## Native Windows

Requirements:
- Python 3.11+
- PostgreSQL (local or external)
- Completed `.env`

Note: RabbitMQ and Celery are not set up by the Windows script. The app falls back to APScheduler (single-process, no background worker). For production use with large corps, Linux or Docker is strongly recommended.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Update:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

## Health check

```
GET /health
```

Returns the status of PostgreSQL and RabbitMQ:

```json
{
  "status": "ok",
  "database": "ok",
  "rabbitmq": "ok"
}
```

## Architecture

```
Browser
  └─► nginx (optional) ──► gunicorn (2–4 workers)
                                └─► FastAPI / Jinja2
                                └─► PostgreSQL (SQLAlchemy)

RabbitMQ ──► Celery Worker (4 concurrency)
                └─► ESI API (ETag-cached, 304-aware)
                └─► DashboardCache table (PostgreSQL)

Celery Beat ──► auto_refresh_stale_accounts  (every 5 min)
            └─► refresh_market_prices_task   (every 15 min)
            └─► cleanup_sso_states_task      (every 1 hour)
```

**Dashboard load flow:**
1. Request hits gunicorn — reads `DashboardCache` from DB (fast, no ESI)
2. If cache is missing or stale: dispatches Celery task and shows loading spinner
3. JS polls `/dashboard/refresh-status?since=<timestamp>` every 3 s
4. When Celery worker finishes and writes updated cache, polling detects it and reloads
5. Subsequent loads serve from cache instantly

## Administrator scripts

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Character Name"
./scripts/add_administrator.py --eve-id 123456789
./scripts/remove_administrator.py --name "Character Name"
./scripts/remove_administrator.py --eve-id 123456789
```

- `add_administrator.py` grants `Administrator` and `Manager` roles
- `remove_administrator.py` removes those roles

## Translations

- GUI translations are loaded from `translation_entries` in the database
- Seed files in `app/locales/` provide the bootstrap content
- Official PI product names are imported from the EVE SDE (`types.json`)
- Static planet details (planet number, radius) are imported from SDE universe data
- SDE-backed entries such as `type.<id>.name` are read-only in the Manager UI

## Tech stack

| Component | Technology |
|---|---|
| Web framework | FastAPI + Jinja2 |
| Database | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| Background jobs | Celery 5 + RabbitMQ |
| Web server | gunicorn + UvicornWorker |
| Frontend | Bootstrap 5 |
| ESI caching | ETag / If-None-Match (HTTP 304) |
| Dev fallback | APScheduler (no RabbitMQ needed) |
| Error tracking | Sentry SDK (optional) |
| Task monitor | Flower (optional, `--profile monitoring`) |

## CCP Notice

EVE Online and all related logos and designs are trademarks or registered trademarks of CCP ehf. This project is not affiliated with, endorsed by, or connected to CCP ehf.

## License

MIT. See [LICENSE](LICENSE).
