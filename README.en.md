# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

Self-hosted Planetary Industry dashboard for EVE Online.

If this project helps you, Ingame-ISK donations to `DrNightmare` are welcome.

## Features

- PI colonies for mains and alts
- Persistent DB caches for market prices, dashboard values, and skyhook values
- Automatic 15-minute refresh for price and value data
- Dashboard with status filters, ISK/day, expiry warnings, and Dotlan links
- Skyhook inventory with history and DB value cache
- PI skills per character in card and list views
- Corporation overview, System Analyzer, Compare, System Mix, and PI Chain Planner
- Manager panel and DB-backed GUI translations for German, English, and Simplified Chinese

## Required ESI scopes

- `esi-planets.manage_planets.v1`
- `esi-planets.read_customs_offices.v1`
- `esi-location.read_location.v1`
- `esi-search.search_structures.v1`
- `esi-characters.read_corporation_roles.v1`
- `esi-skills.read_skills.v1`

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

Notes:

- `DATABASE_URL`: PostgreSQL connection string used by the app
- `EVE_CLIENT_ID` / `EVE_CLIENT_SECRET`: from the CCP Developer Portal
- `EVE_CALLBACK_URL`: must exactly match the redirect URL configured in the CCP app
- `SECRET_KEY`: used for session signing and should always be replaced

### Commonly adjusted values

```env
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
APP_PORT=8000
DEBUG=false
JANICE_API_KEY=
DB_PASSWORD=
```

- `EVE_SCOPES`: requested ESI permissions
- `APP_PORT`: local application port
- `DEBUG`: enable only for development
- `JANICE_API_KEY`: optional
- `DB_PASSWORD`: mainly useful for Compose/container setups

### Example

```env
DATABASE_URL=postgresql://evepi:supersecret@localhost/evepi
EVE_CLIENT_ID=1234567890abcdef
EVE_CLIENT_SECRET=abcdef1234567890
EVE_CALLBACK_URL=http://192.168.2.44/auth/callback
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
SECRET_KEY=replace_me_with_a_long_random_secret_key
APP_PORT=8000
DEBUG=false
```

### After `.env` changes

- restart the service or container
- after scope changes, affected characters must re-authorize through EVE SSO
- wrong `EVE_CALLBACK_URL` or `EVE_SCOPES` usually break login or scope refresh immediately

## Docker Compose

```bash
docker compose up -d
```

Update:

```bash
git pull origin main
docker compose pull
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

Or use the bundled update script directly:

```bash
bash scripts/update_linux.sh --compose
```

If you update directly from the local working tree, this is usually enough:

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
```

Recommended order:

- fetch the new code
- pull or rebuild images
- restart the containers
- run migrations with `alembic upgrade head`
- then check the logs

Log check:

```bash
docker compose logs -n 100 app
```

Administrator scripts in Compose:

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789
docker compose exec app python /app/scripts/remove_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/remove_administrator.py --eve-id 123456789
```

## Linux

```bash
chmod +x scripts/setup_linux.sh
bash scripts/setup_linux.sh
```

Update:

```bash
bash ~/PI_Manager/scripts/update_linux.sh
```

Optional:

- use `--branch <name>` to update from another branch
- use `--compose` when this checkout is your Docker Compose deployment

## Native Windows

Yes, the system can run natively on Windows.

Requirements:

- Python 3.11+
- local or external PostgreSQL
- completed `.env`

Setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Update:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

Docker Compose on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Compose
```

Optional:

- use `-Branch <name>` to update from another branch

Note:

- The application itself runs natively on Windows.
- Linux-specific comfort pieces such as `systemd` only apply to Linux hosts.
- Linux or Docker is still the stronger production recommendation.

## Administrator scripts

Directly on the host:

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Character Name"
./scripts/add_administrator.py --eve-id 123456789
./scripts/remove_administrator.py --name "Character Name"
./scripts/remove_administrator.py --eve-id 123456789
```

Effect:

- `add_administrator.py` grants `Administrator` and `Manager`
- `remove_administrator.py` removes `Administrator` and `Manager`

## Translations

- GUI translations are loaded from `translation_entries` in the database
- seed files in `app/locales/` provide the bootstrap content
- official PI product names are imported from the EVE SDE (`types.json`)
- API/SDE-backed entries such as `type.<id>.name` are intentionally read-only in the Manager UI

## Deployment workflow

- fast UI/template testing runs on `192.168.2.44` (`pitest`)
- persistent changes are committed and pushed to `main`
- production-like updates should then be applied via the existing update script

## Tech stack

- Python 3.11
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0
- Alembic
- Jinja2
- Bootstrap 5
- APScheduler

## License

MIT. See [LICENSE](LICENSE).
