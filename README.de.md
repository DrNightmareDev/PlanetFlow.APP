# PlanetFlow

[Deutsch](README.de.md) | [English](README.en.md) | [ZH-CN](README.zh-Hans.md)

`PlanetFlow` ist eine selbst gehostete EVE-Online-PI-Plattform, die das klassische Dashboard um Billing, seitenbasierte Zugriffskontrolle und ein produktionsnahes Docker-Deployment mit TLS erweitert.

> **[planetflow.app](https://planetflow.app)** — gehostete Version ausprobieren oder direkt mit dem Self-Hosting loslegen.

Wenn dir das Projekt hilft, freue ich mich über Ingame-ISK an `DrNightmare`.

## Funktionsumfang

- Dashboard mit Koloniestatus, Ablaufzeiten, ISK/Tag, CSV-Export und Paginierung
- Werkzeuge für Characters, Corporation, Inventory, Hauling, Intel, Killboard, Skyhooks und Templates
- PI Chain Planner, Colony Assignment Planner, System Analyzer, System Mix, Compare und Fittings
- Billing-Seiten und zugriffsgesteuerte Routen im UI
- Hintergrundjobs über Celery + RabbitMQ
- HTTPS-Deployment mit nginx + certbot in Docker Compose
- UI-Übersetzungen für Deutsch, Englisch und vereinfachtes Chinesisch

## Hauptseiten

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
- `Admin`
- `Director`

## Benötigte ESI-Scopes

```text
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
esi-fittings.read_fittings.v1
```

Für strukturbezogene Abläufe ist `esi-search.search_structures.v1` zusätzlich empfehlenswert.

## Schnellstart

```bash
cp .env.example .env
docker compose up -d
```

Mindestens diese Werte setzen:

```env
DB_PASSWORD=change_me
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=https://planetflow.app/auth/callback
SECRET_KEY=replace_me_with_a_long_random_secret_key
RABBITMQ_PASS=change_me_rabbit
```

Wichtige Hinweise:

- `COOKIE_SECURE=true`, wenn die Anwendung über HTTPS ausgeliefert wird
- Das Standard-Compose-Setup enthält bereits `nginx` und `certbot`
- `sde_init` bereitet das beschreibbare SDE-Volume für den App-User vor

## Docker-Compose-Dienste

- `db`
- `rabbitmq`
- `sde_init`
- `app`
- `celery_worker`
- `celery_wallet`
- `celery_beat`
- `nginx`
- `certbot`

Optionale Profile:

- `pgbouncer`
- `monitoring`

Nützliche Befehle:

```bash
docker compose up -d
docker compose logs -f app
docker compose logs -f celery_worker
docker compose logs -f celery_wallet
docker compose logs -f celery_beat
docker compose ps
```

## Skripte

Frischer Hetzner-Ubuntu-Server:

```bash
bash scripts/setup_hetzner.sh
```

Konfiguration prüfen, Zertifikate holen und Stack starten:

```bash
bash scripts/start.sh
```

Bestehendes Deployment aktualisieren:

```bash
bash scripts/update.sh
```

## Administrator-Werkzeuge

- `scripts/add_administrator.py`
- `scripts/remove_administrator.py`
- Admin-Seiten in der App für Zugriffspolitik, Account-Verwaltung und Übersetzungen

## Health-Check

```text
GET /health
```

Der Endpunkt liefert den Status von Datenbank und RabbitMQ und wird von den Container-Healthchecks verwendet.

## Tech Stack

- FastAPI + Jinja2
- PostgreSQL + SQLAlchemy + Alembic
- Celery + RabbitMQ
- Gunicorn / Uvicorn
- nginx + certbot
- Bootstrap 5

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
