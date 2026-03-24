# EVE PI Manager

Planetary Industry Dashboard fuer EVE Online - selbst gehostet.

Wenn dir das Projekt hilft, freue ich mich ueber Ingame-ISK-Spenden an `DrNightmare`.

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
- Seitenuebergreifende System-Favoriten in System Analyzer, Vergleich, System Mix und PI Chain Planner
- Manager Panel und Zugangspolitik
- DB-basierte GUI-Uebersetzungen fuer Deutsch, Englisch und vereinfachtes Chinesisch
- Manager-Uebersetzungstabelle mit Filter fuer Website-Texte und gesperrten API-/SDE-Eintraegen
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

Danach entweder Docker Compose, das Linux-Setup oder das native Windows-Setup verwenden.

### Docker Compose

```bash
docker compose up -d
```

Administrator-Skripte im Compose-Setup:

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Charaktername"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789

docker compose exec app python /app/scripts/remove_administrator.py --name "Charaktername"
docker compose exec app python /app/scripts/remove_administrator.py --eve-id 123456789
```

### Linux

Fuer Debian/Ubuntu-aehnliche Hosts oder Linux-Container:

```bash
chmod +x scripts/setup_linux.sh
bash scripts/setup_linux.sh
```

### Windows nativ

Ja, das System kann auch nativ unter Windows laufen.

Voraussetzungen:

- Python 3.11+
- PostgreSQL lokal oder extern
- eine ausgefuellte `.env`

Setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Update:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

Hinweis:

- Die Anwendung selbst laeuft nativ unter Windows.
- Die Linux-Komfortteile wie `systemd`, `nginx` und die Linux-Host-Skripte sind naturgemaess nur fuer Linux gedacht.
- Fuer Produktion bleibt Linux oder Docker die robustere Empfehlung.

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

## Update (Linux)

```bash
bash ~/PI_Manager/scripts/update_linux.sh
```

Das Script zieht `main`, installiert Abhaengigkeiten, fuehrt Migrationen aus und startet den Service neu.

## Administrator-Skripte

Direkt auf dem Host:

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Charaktername"
./scripts/add_administrator.py --eve-id 123456789

./scripts/remove_administrator.py --name "Charaktername"
./scripts/remove_administrator.py --eve-id 123456789
```

Wirkung:

- `add_administrator.py` setzt den gefundenen Account auf `Administrator` und `Manager`
- `remove_administrator.py` entfernt `Administrator` und `Manager` vom gefundenen Account

## Deployment-Workflow

- Schnelle UI-/Template-Tests laufen auf `192.168.2.44` (`pitest`).
- Dauerhafte Stände werden per Commit nach `main` gepusht.
- Produktive oder persistente Updates sollen anschliessend ueber das vorhandene Update-Script eingespielt werden.
- Nach Template-Aenderungen immer kurz Service-Status und Logs pruefen.

## Setup-Skripte

- `scripts/setup_linux.sh`: Vollstaendiges Setup fuer Linux-Hosts oder Linux-Container
- `scripts/update_linux.sh`: Update-Skript fuer bestehende Linux-Installationen
- `scripts/setup_windows.ps1`: Basis-Setup fuer nativen Windows-Betrieb
- `scripts/update_windows.ps1`: Update-Skript fuer nativen Windows-Betrieb

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

## Uebersetzungen

- GUI-Uebersetzungen werden aus `translation_entries` in der Datenbank geladen.
- Seed-Dateien unter `app/locales/` liefern den Bootstrap-Bestand.
- Offizielle PI-Produktnamen werden aus der EVE SDE (`types.json`) in die DB uebernommen.
- API-/SDE-basierte Eintraege wie `type.<id>.name` sind im Manager absichtlich schreibgeschuetzt.
- Nur Website-eigene Texte sollen im Manager-Editor bearbeitet werden.

## Hinweise

- Die fruehere Gesamt-Uebersicht wurde entfernt.
- Preisaktualisierungen laufen zentral ueber den Scheduler.
- System-Favoriten werden browserseitig gemeinsam genutzt, damit dieselben Systeme in `System Analyzer`, `Vergleich`, `System Mix` und `PI Chain Planner` direkt wieder angeklickt werden koennen.
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
