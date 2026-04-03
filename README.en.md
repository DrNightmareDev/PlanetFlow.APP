# PlanetFlow

[Deutsch](README.de.md) | [English](README.en.md) | [ZH-CN](README.zh-Hans.md)

`PlanetFlow` is a self-hosted EVE Online PI platform that extends the classic dashboard with billing, page-based access control, and a production-ready Docker deployment with TLS.

If this project is useful to you, in-game ISK donations to `DrNightmare` are welcome.

## Features

- Dashboard with colony status, expiry timers, ISK/day, CSV export, and pagination
- Characters, Corporation, Inventory, Hauling, Intel, Killboard, Skyhooks, and Templates
- PI Chain Planner, Colony Assignment Planner, System Analyzer, System Mix, Compare, and Fittings
- Billing pages and page-based access control built into the UI
- Background jobs via Celery + RabbitMQ
- HTTPS deployment with nginx + certbot in Docker Compose
- UI translations for German, English, and Simplified Chinese

## Pages

- `Dashboard`
- `Characters`
- `Corporation`
- `Inventory`
- `Hauling`
- `Intel`
- `Killboard`
- `Skyhooks`
- `PI Templates`
- `Jita Market`
- `PI Chain Planner`
- `Colony Assignment Planner`
- `System Analyzer`
- `System Mix`
- `Compare`
- `Fittings`
- `Billing`
- `Administration`
- `Director`

## Required ESI Scopes

```text
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
esi-fittings.read_fittings.v1
```

`esi-search.search_structures.v1` is additionally recommended for structure-related workflows.

## Quick Start

```bash
cp .env.example .env
docker compose up -d
```

Set at minimum these values:

```env
DB_PASSWORD=change_me
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=https://your-domain/auth/callback
SECRET_KEY=replace_me_with_a_long_random_secret_key
RABBITMQ_PASS=change_me_rabbit
```

Notes:

- Set `COOKIE_SECURE=true` when serving over HTTPS
- The default Compose setup already includes `nginx` and `certbot`
- `sde_init` prepares the writable SDE volume for the app user

## Docker Compose Services

- `db`
- `rabbitmq`
- `sde_init`
- `app`
- `celery_worker`
- `celery_wallet`
- `celery_beat`
- `nginx`
- `certbot`

Optional profiles:

- `pgbouncer`
- `monitoring`

Useful commands:

```bash
docker compose up -d
docker compose logs -f app
docker compose logs -f celery_worker
docker compose logs -f celery_wallet
docker compose logs -f celery_beat
docker compose ps
```

## Scripts

Fresh Hetzner Ubuntu server:

```bash
bash scripts/setup_hetzner.sh
```

Validate config, obtain TLS certificates, and start the stack:

```bash
bash scripts/start.sh
```

Update an existing deployment:

```bash
bash scripts/update.sh
```

## Administrator Tools

- `scripts/add_administrator.py`
- `scripts/remove_administrator.py`
- In-app Administration pages for access policy, account management, and translations

## Health Check

```text
GET /health
```

Returns the status of the database and RabbitMQ. Also used by the container health checks.

## Tech Stack

- FastAPI + Jinja2
- PostgreSQL + SQLAlchemy + Alembic
- Celery + RabbitMQ
- Gunicorn / Uvicorn
- nginx + certbot
- Bootstrap 5

## License

MIT. See [LICENSE](LICENSE).
