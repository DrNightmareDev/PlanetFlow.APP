# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

Self-hosted Planetary Industry dashboard for EVE Online.

If this project helps you, in-game ISK donations to `DrNightmare` are welcome.

---

## Features

- PI colonies for mains and alts across any number of accounts and characters
- **Celery + RabbitMQ background refresh** — ESI data is fetched in the background every 5 minutes; the dashboard always loads instantly from cache
- **ETag-based ESI caching** — unchanged planets return HTTP 304 and skip re-processing (~60–70% fewer ESI calls after the first run)
- Persistent DB caches for market prices, dashboard data, skyhook values, ETag responses, GUI translations, and static planet details
- Automatic 15-minute market price refresh and 5-minute colony refresh via Celery Beat
- Dashboard with status filters, ISK/day, **live expiry countdown** (updates every minute in the browser without page reload), extractor balance indicators, extractor-rate filters, tier filtering, auto-refresh countdown, and Dotlan links
- **Pagination** — client-side pagination (default 50/page, configurable up to All) for large colony lists
- **Discord / Webhook alerts** — server-side colony expiry notifications via Discord webhooks or compatible endpoints, configurable per account with cooldown; Discord rate-limiting (429) is handled automatically
- **Token status overview** — dashboard banner and per-character view show expired or missing tokens; auto-retry after 24 h; banner only appears for genuine auth problems, not already-resolved errors
- **Corporation page async** — uncached corp accounts are automatically dispatched to Celery background tasks
- **CSV export** — download the full colony list as CSV from the dashboard
- **Mobile-responsive view** — compact table layout for small screens with horizontal scroll
- **PI Templates** — save, share, and import colony layouts with to-scale canvas rendering and community templates from GitHub
- Skyhook inventory with history and DB value cache
- PI skills per character in card and list views
- Corporation overview, System Analyzer, Compare, System Mix, and PI Chain Planner
- Manager panel and DB-backed GUI translations for German, English, and Simplified Chinese
- Optional **Sentry** error tracking and **Flower** Celery task monitor

## UI Pages

| Page | Description |
|---|---|
| `Dashboard` | All PI colonies, daily ISK value, live expiry countdown, storage state, skyhook context, auto-refresh countdown, filters for Active/Expired/Stalled/Balanced/Unbalanced/Extractor-Rate |
| `PI Templates` | Canvas editor for colony layouts with to-scale planet rendering and import from community GitHub sources |
| `Skyhooks` | Edit and save skyhook inventories per planet, history view, value calculation |
| `Characters` | All linked characters, main/alt assignments, token status, PI skills in card and list views |
| `Corporation` | Corporation PI data summarized: mains, colonies, PI types, product search |
| `Jita Market` | Buy/sell prices, spreads, trends, and trade volume for PI products from cached Jita market data |
| `PI Chain Planner` | Build full production chains for P1-P4, show required planet types, P0 raw materials, and suitable systems |
| `System Analyzer` | Analyze a single system: planet types, P0 resources, PI recommendations, planet details |
| `System Mix` | Combine multiple systems or constellations and show achievable PI products |
| `Compare` | Compare multiple systems side by side |
| `Fittings` | Compare ESI fittings of all characters side by side (requires `esi-fittings.read_fittings.v1` scope) |

## Required ESI Scopes

```
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-search.search_structures.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
```

Optional (for Fittings comparison):
```
esi-fittings.read_fittings.v1
```

## Quick Start

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
- `SECRET_KEY`: used for session signing — replace with a secure random value (min. 32 characters). The app will not start if the default value is left unchanged.

> **Note:** The app refuses to start if `EVE_CLIENT_ID`, `EVE_CLIENT_SECRET`, or `SECRET_KEY` are not configured.

### RabbitMQ / Celery (required for background refresh)

```env
RABBITMQ_USER=evepi
RABBITMQ_PASS=change_me_rabbit
CELERY_BROKER_URL=amqp://evepi:change_me_rabbit@rabbitmq:5672//
```

- Use `@rabbitmq:5672` for Docker Compose, `@localhost:5672` for native Linux installs.
- Leave `CELERY_BROKER_URL` empty to run without Celery (APScheduler fallback, single-process — not recommended for large corps).

### Performance

```env
# Each gunicorn worker loads the full app (~400-500 MB).
# With 2 GB RAM use 2 workers, with 4+ GB RAM use 2-4 workers.
WEB_WORKERS=2

# Database connection pool (optional, defaults are sufficient for small instances)
DB_POOL_SIZE=5
DB_POOL_OVERFLOW=10
DB_POOL_RECYCLE=3600
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

Starts the core stack: **PostgreSQL**, **RabbitMQ**, the **web app** (gunicorn), **Celery worker**, and **Celery Beat** scheduler.

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
bash scripts/update_compose.sh
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
| `eve-pi-manager-beat` | Celery Beat scheduler (triggers refresh every 5 min) |

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

> Note: RabbitMQ and Celery are not set up by the Windows script. The app falls back to APScheduler (single-process, no background worker). For production use with large corps, Linux or Docker is strongly recommended.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Update:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

## Health Check

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

This endpoint is also used by the Docker healthcheck of the `app` container.

## Architecture

```
Browser
  └─► nginx (optional) ──► gunicorn (2–4 workers)
                                └─► FastAPI / Jinja2
                                └─► PostgreSQL (SQLAlchemy 2.0)

RabbitMQ ──► Celery Worker (4 concurrency, prefetch=1)
                └─► ESI API (ETag-cached, 304-aware, Error-Budget-Guard)
                └─► DashboardCache table (PostgreSQL)

Celery Beat ──► auto_refresh_stale_accounts  (every 5 min)
            └─► refresh_market_prices_task   (every 15 min)
            └─► send_webhook_alerts_task     (every 15 min)
            └─► cleanup_sso_states_task      (every 1 hour)
```

**Dashboard load flow:**
1. Request hits gunicorn — reads `DashboardCache` from DB (fast, no ESI)
2. If cache is missing or stale: dispatches Celery task and shows loading spinner
3. JS polls `/dashboard/refresh-status?since=<timestamp>` every 3 s
4. When Celery worker finishes and writes updated cache, polling detects it and reloads
5. Subsequent loads serve from cache instantly

**ESI error handling:**
- Token refresh fails: up to 3 retries with exponential backoff (2s, 4s); 401/403 fail immediately as permanent
- ESI error budget: `X-ESI-Error-Limit-Remain` header is checked after every call; if below 20 remaining, waits 10s
- Characters with >= 3 consecutive errors are skipped for 24h, then automatically reset

## Manager Panel

The Manager Panel (`/manager`) is for administrators:

- **Account management**: View all accounts and characters, grant/revoke manager role, delete accounts, impersonate accounts
- **Access policy**: Configure allowlist or blocklist for corporations and alliances
- **Reset ESI errors**: Characters with ESI errors are shown with a red badge; the ↺ button resets `esi_consecutive_errors` immediately without waiting
- **Reload colony cache**: Manually refresh the dashboard cache for any account
- **Translations**: Edit custom GUI translations directly in the Manager

## Security Notes

- **Cookies**: Session cookies are `httponly`, `samesite=lax`, and automatically `secure` in production (`DEBUG=false`)
- **Webhooks**: Only Discord webhook URLs are accepted (`discord.com/api/webhooks/…`); other URLs are rejected server-side
- **Startup validation**: App refuses to start without `SECRET_KEY`, `EVE_CLIENT_ID`, and `EVE_CLIENT_SECRET`
- **Docker**: App container runs as unprivileged user `appuser` (uid 1000)
- **Error messages**: Raw exception details are never sent to the browser; all errors are logged server-side

## Administrator Scripts

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

- GUI translations are loaded from `translation_entries` in the database (in-memory cached, invalidated on change)
- Seed files in `app/locales/` provide the bootstrap content
- Official PI product names are imported from the EVE SDE (`types.json`)
- Static planet details (planet number, radius) are imported from SDE universe data
- SDE-backed entries such as `type.<id>.name` are read-only in the Manager UI

## Tech Stack

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
