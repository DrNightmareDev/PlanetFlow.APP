# PlanetFlow — English

[Deutsch](README.de.md) | [English](README.en.md) | [ZH-CN](README.zh-Hans.md)

Self-hosted Planetary Industry platform for EVE Online.

> **[planetflow.app](https://planetflow.app)** — try the hosted version, or self-host it yourself.

If this project is useful to you, in-game ISK donations to `DrNightmare` are welcome.

---

## Migrating from EVE PI Manager?

EVE PI Manager has been archived. PlanetFlow is its successor — all features have been ported and extended.

**Your data does not carry over automatically.** The two apps use separate databases. The migration is straightforward:

### Step 1 — Set up PlanetFlow fresh

Follow the [Local Setup](#local-setup-your-pc-no-domain) or [Server Setup](#server-setup-with-domain--https) below. PlanetFlow is a clean install — you just need your EVE SSO login.

### Step 2 — Register a new EVE developer application (or reuse your existing one)

- If you already have an EVE developer app for EVE PI Manager, you can reuse it — just update the **Callback URL** to point to PlanetFlow.
- Or create a new app at [https://developers.eveonline.com](https://developers.eveonline.com). The required scopes are identical.

### Step 3 — Log in and re-add your characters

PlanetFlow uses the same EVE SSO flow. Log in, go to Characters, and re-authorise each character. ESI data syncs automatically in the background — your colonies, expiry timers, and planet data will appear within a few minutes.

### Step 4 — Re-enter any manual data

Items that were stored locally in EVE PI Manager and are not pulled from ESI need to be re-entered manually:
- **Inventory lots** — re-add via the Inventory page
- **Hauling routes / bridge connections** — re-configure in Hauling
- **Skyhook entries** — re-enter in Skyhooks
- **PI templates** — re-upload in PI Templates

### Step 5 — Shut down EVE PI Manager

Once PlanetFlow is running and synced:
```bash
# in your old eve-pi-manager directory
docker compose down
```

You can keep the old database volume as a backup, or remove it entirely:
```bash
docker compose down -v   # removes volumes too — irreversible
```

### Key differences from EVE PI Manager

| | EVE PI Manager | PlanetFlow |
|---|---|---|
| HTTPS / TLS | Optional nginx profile | Built-in (Let's Encrypt or proxy mode) |
| Billing & access control | Not present | Built-in |
| Admin panel | `/manager` | `/admin` |
| Background workers | Celery + APScheduler fallback | Celery only (RabbitMQ required) |
| Config key | `CELERY_BROKER_URL` | `RABBITMQ_USER` / `RABBITMQ_PASS` |
| Local HTTP mode | `COOKIE_SECURE=false` | `COOKIE_SECURE=false` + `NGINX_MODE=proxy` |

---

## Prerequisites

You need **Docker Desktop** installed. That's it.

- Windows / Mac: [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
- Linux: install `docker` + `docker compose` via your package manager

---

## Step 1 — Create an EVE Developer Application

Before starting, you need an EVE Online API key.

1. Go to [https://developers.eveonline.com](https://developers.eveonline.com) and log in
2. Click **Create New Application**
3. Fill in:
   - **Name:** anything you like (e.g. `My PlanetFlow`)
   - **Connection Type:** `Authentication & API Access`
   - **Callback URL:**
     - Local setup: `http://localhost/auth/callback`
     - Server with domain: `https://yourdomain.com/auth/callback`
   - **Scopes** — add all of these:
     ```
     esi-planets.manage_planets.v1
     esi-planets.read_customs_offices.v1
     esi-location.read_location.v1
     esi-characters.read_corporation_roles.v1
     esi-skills.read_skills.v1
     esi-fittings.read_fittings.v1
     ```
4. Save and copy your **Client ID** and **Client Secret**

---

## Local Setup (your PC, no domain)

This runs PlanetFlow on your own computer at `http://localhost`.  
No domain, no TLS certificate, no nginx configuration needed.

### 1. Download the project

```bash
git clone https://github.com/your-org/planetflow.app.git
cd planetflow.app
```

### 2. Create your config file

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in these values:

```env
# A password for the internal database — pick anything
DB_PASSWORD=my_local_password

# Your EVE character ID (find it at https://evewho.com)
EVE_OWNER_CHARACTER_ID=123456789

# From your EVE developer application (Step 1)
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=http://localhost/auth/callback

# Generate a random secret key:
# Run this in your terminal: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=paste_your_generated_key_here

# A password for the internal message queue — pick anything
RABBITMQ_PASS=my_local_rabbit_password

# IMPORTANT for local: must be false (no HTTPS locally)
COOKIE_SECURE=false

# IMPORTANT for local: use proxy mode — no TLS certificate needed
NGINX_MODE=proxy
```

Leave everything else as-is.

### 3. Start

```bash
docker compose up -d
```

Docker will download and build everything automatically. This takes a few minutes the first time.

### 4. Open in browser

[http://localhost](http://localhost)

Log in with EVE SSO. The first account to log in becomes the Owner (admin).

### 5. Stop

```bash
docker compose down
```

---

## Server Setup (with domain + HTTPS)

### 1. Prepare your server

On a fresh Ubuntu server (tested on Hetzner):

```bash
bash scripts/setup_hetzner.sh
```

This installs Docker and all dependencies.

### 2. Create your config file

```bash
cp .env.example .env
nano .env
```

Required values:

```env
DB_PASSWORD=strong_database_password
EVE_OWNER_CHARACTER_ID=123456789
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=https://yourdomain.com/auth/callback
SECRET_KEY=your_generated_secret_key
RABBITMQ_PASS=strong_rabbit_password
COOKIE_SECURE=true
NGINX_MODE=https
```

### 3. Start (with automatic TLS)

```bash
bash scripts/start.sh
```

This validates your config, obtains a Let's Encrypt certificate, and starts all services.

### 4. Update an existing deployment

```bash
bash scripts/update.sh
```

---

## Granting Admin Access

After logging in for the first time, run:

```bash
docker compose exec app python scripts/add_administrator.py
```

---

## Useful Commands

```bash
# View logs
docker compose logs -f app
docker compose logs -f celery_worker

# Status of all containers
docker compose ps

# Restart everything
docker compose restart

# Stop and remove containers (data is preserved in volumes)
docker compose down
```

---

## Troubleshooting

**Login does not work / callback error**
- Make sure `EVE_CALLBACK_URL` in `.env` exactly matches what you registered on the EVE developer portal
- For local: must be `http://localhost/auth/callback` (not https)
- For local: `COOKIE_SECURE` must be `false`

**Page loads but shows no data**
- Check the app logs: `docker compose logs -f app`
- Check the worker logs: `docker compose logs -f celery_worker`

**Port 80 already in use**
- Another program (IIS, another web server) is using port 80. Stop it, or change the port in `docker-compose.yml`

**"Connection refused" on http://localhost**
- Wait 30–60 seconds after `docker compose up -d` — the app needs time to start
- Check: `docker compose ps` — all services should show `healthy` or `running`

---

## Services Overview

| Service | What it does |
|---|---|
| `db` | PostgreSQL database |
| `rabbitmq` | Message queue for background jobs |
| `app` | The web application |
| `celery_worker` | Background job worker (ESI sync, etc.) |
| `celery_beat` | Job scheduler (runs tasks on a timer) |
| `nginx` | Web server / reverse proxy |
| `certbot` | Automatic TLS certificate renewal (server only) |

---

## Features

- Dashboard with colony status, expiry timers, ISK/day, CSV export
- Characters, Corporation, Inventory, Hauling, Intel, Killboard, Skyhooks, Templates
- PI Chain Planner, Colony Assignment Planner, System Analyzer, Compare, Fittings
- Billing and page-based access control
- German, English, and Simplified Chinese UI

## Tech Stack

- FastAPI + PostgreSQL + Celery + RabbitMQ + nginx
- Bootstrap 5 · Deployed via Docker Compose

## License

MIT. See [LICENSE](LICENSE).
