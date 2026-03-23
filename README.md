# EVE PI Manager

Planetary Industry Dashboard fuer EVE Online - selbst gehostet.

## Features

- PI Kolonien fuer Main und Alts
- Persistente DB-Caches fuer Marktpreise, Dashboard-Daten und Skyhook-Werte
- Automatischer 15-Minuten-Refresh fuer Preis- und Wertdaten
- Dashboard mit Aktiv/Inaktiv-Filtern, ISK/Tag, Ablaufwarnungen und Dotlan-Links aus der Orts-Spalte
- Skyhook Inventar mit Verlauf und DB-Wert-Cache
- PI Skills pro Charakter in Karten- und Listenansicht
- Sortierbare Charakter-Liste in der Listenansicht
- Corporation-Uebersicht mit Main-Tabelle und Pagination
- System Analyzer, System Vergleich und PI Chain Planner
- Admin Panel und Zugangspolitik
- Globales Storage-Icon fuer letzte Preisaktualisierung

## Benoetigte ESI-Scopes

- `esi-planets.manage_planets.v1`
- `esi-planets.read_customs_offices.v1`
- `esi-location.read_location.v1`
- `esi-search.search_structures.v1`
- `esi-characters.read_corporation_roles.v1`
- `esi-skills.read_skills.v1`

## Schnellstart

```bash
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager
cp .env.example .env
nano .env
```

Danach entweder Docker Compose oder das LXC-Setup verwenden.

### Docker Compose

```bash
docker compose up -d
```

### Proxmox LXC

```bash
chmod +x scripts/setup_lxc.sh
bash scripts/setup_lxc.sh
```

## Wichtige `.env` Eintraege

```env
EVE_CLIENT_ID=dein_client_id
EVE_CLIENT_SECRET=dein_client_secret
EVE_CALLBACK_URL=http://DEINE-IP/auth/callback
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
SECRET_KEY=min_32_zeichen_zufaelliger_schluessel
DATABASE_URL=postgresql://evepi:PASSWORT@localhost/evepi
DB_PASSWORD=PASSWORT
```

## Update (LXC)

```bash
bash ~/PI_Manager/scripts/update_lxc.sh
```

Das Script zieht `main`, installiert Abhaengigkeiten, fuehrt Migrationen aus und startet den Service neu.

## Deployment-Workflow

- Schnelle UI-/Template-Tests laufen auf `192.168.2.44` (`pitest`).
- Dauerhafte Stände werden per Commit nach `main` gepusht.
- Produktive oder persistente Updates sollen anschliessend ueber das vorhandene Update-Script eingespielt werden.
- Nach Template-Aenderungen immer kurz Service-Status und Logs pruefen.

## Service-Verwaltung

```bash
systemctl status eve-pi-manager
journalctl -u eve-pi-manager -f
systemctl restart eve-pi-manager
```

## Datenbank-Caches

- `market_cache`
- `dashboard_cache_db`
- `skyhook_value_cache`

## Hinweise

- Die fruehere Gesamt-Uebersicht wurde entfernt.
- Preisaktualisierungen laufen zentral ueber den Scheduler.
- Templates sollten als UTF-8 ohne BOM gespeichert werden.

## Tech Stack

- Python 3.11
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0
- Alembic
- Jinja2
- Bootstrap 5
- APScheduler

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
