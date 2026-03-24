# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

Planetary Industry Dashboard fuer EVE Online - selbst gehostet.

Wenn dir das Projekt hilft, freue ich mich ueber Ingame-ISK-Spenden an `DrNightmare`.

## Features

- PI Kolonien fuer Main und Alts
- Persistente DB-Caches fuer Marktpreise, Dashboard-Daten und Skyhook-Werte
- Automatischer 15-Minuten-Refresh fuer Preis- und Wertdaten
- Dashboard mit Statusfiltern, ISK/Tag, Ablaufwarnungen, Extraktor-Balance-Indikatoren, einstellbarem Extraktor-Ratenfilter und Dotlan-Links
- Skyhook Inventar mit Verlauf und DB-Wert-Cache
- PI Skills pro Charakter in Karten- und Listenansicht
- Corporation-Uebersicht, System Analyzer, Vergleich, System Mix und PI Chain Planner
- Manager Panel und DB-basierte GUI-Uebersetzungen fuer Deutsch, Englisch und vereinfachtes Chinesisch

## Seiten im UI

- `Dashboard`: Zeigt alle PI-Kolonien, den taeglichen ISK-Wert, Ablaufzeiten, Storage-Staende, Skyhook-Bezug und Filter fuer Aktiv, Abgelaufen, Stalled, Balanced, Unbalanced und Extraktor-Raten.
- `Skyhooks`: Erlaubt das Bearbeiten und Speichern von Skyhook-Bestaenden pro Planet inklusive Verlaufsansicht und Wertberechnung.
- `Characters`: Zeigt alle verknuepften Charaktere, Main/Alt-Zuordnung, Token-Status und PI-Skills in Karten- und Listenansicht.
- `Corporation`: Fasst die PI-Daten der eigenen Corporation zusammen, zeigt Main-Charaktere, Kolonien, PI-Typen und Produktsuche ueber alle Corp-Kolonien.
- `Jita Markt`: Zeigt Kauf-/Verkaufspreise, Spreads, Trends und Handelsvolumen fuer PI-Produkte aus dem Jita-/The-Forge-Marktcache.
- `PI Chain Planner`: Baut Produktionsketten fuer P1-P4 auf, zeigt benoetigte Planetentypen, P0-Rohstoffe und passende Systeme fuer ein Produkt.
- `System Analyzer`: Analysiert ein einzelnes System, zeigt verfuegbare Planetentypen, P0-Ressourcen und daraus abgeleitete PI-Empfehlungen.
- `System Mix`: Kombiniert mehrere Systeme oder Konstellationen und zeigt, welche PI-Produkte mit den gemeinsamen Planetentypen moeglich sind.
- `Vergleich`: Vergleicht mehrere Systeme direkt nebeneinander, inklusive Planetentypen und PI-Empfehlungen.

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

Hinweise:

- `DATABASE_URL`: PostgreSQL-Verbindungsstring der App
- `EVE_CLIENT_ID` / `EVE_CLIENT_SECRET`: aus dem CCP Developer Portal
- `EVE_CALLBACK_URL`: muss exakt zur in der CCP-App eingetragenen Redirect-URL passen
- `SECRET_KEY`: fuer Session-Signierung, unbedingt durch einen eigenen sicheren Wert ersetzen

### Wichtige anpassbare Werte

```env
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
APP_PORT=8000
DEBUG=false
JANICE_API_KEY=
DB_PASSWORD=
```

- `EVE_SCOPES`: angeforderte ESI-Berechtigungen
- `APP_PORT`: Port der lokalen App
- `DEBUG`: nur fuer Entwicklung auf `true`
- `JANICE_API_KEY`: optional
- `DB_PASSWORD`: vor allem fuer Compose-/Container-Setups

### Beispiel

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

### Nach `.env`-Aenderungen

- Service oder Container neu starten
- bei Scope-Aenderungen muessen sich betroffene Charaktere erneut ueber EVE SSO autorisieren
- falsche `EVE_CALLBACK_URL` oder `EVE_SCOPES` fuehren typischerweise sofort zu Login- oder Scope-Fehlern

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

Alternativ direkt mit dem mitgelieferten Update-Skript:

```bash
bash scripts/update_linux.sh --compose
```

Falls du direkt aus dem lokalen Working Tree aktualisierst, reicht meist:

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
```

Empfohlene Reihenfolge:

- neuen Code holen
- Images neu bauen oder ziehen
- Container neu starten
- Migrationen mit `alembic upgrade head` ausfuehren
- danach kurz Logs pruefen

Log-Check:

```bash
docker compose logs -n 100 app
```

Administrator-Skripte im Compose-Setup:

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Charaktername"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789
docker compose exec app python /app/scripts/remove_administrator.py --name "Charaktername"
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

- mit `--branch <name>` von einem anderen Branch aktualisieren
- mit `--compose` denselben Script fuer ein Docker-Compose-Deployment nutzen

## Windows nativ

Ja, das System kann auch nativ unter Windows laufen.

Voraussetzungen:

- Python 3.11+
- PostgreSQL lokal oder extern
- ausgefuellte `.env`

Setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Update:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

Docker Compose unter Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Compose
```

Optional:

- mit `-Branch <name>` von einem anderen Branch aktualisieren

Hinweis:

- Die Anwendung selbst laeuft nativ unter Windows.
- Komfortteile wie `systemd` und klassische Linux-Host-Setups gelten nur fuer Linux.
- Fuer Produktion bleiben Linux oder Docker robuster.

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
- `remove_administrator.py` entfernt `Administrator` und `Manager`

## Uebersetzungen

- GUI-Uebersetzungen werden aus `translation_entries` in der Datenbank geladen
- Seed-Dateien unter `app/locales/` liefern den Bootstrap-Bestand
- Offizielle PI-Produktnamen werden aus der EVE SDE (`types.json`) in die DB uebernommen
- API-/SDE-Eintraege wie `type.<id>.name` sind im Manager schreibgeschuetzt

## Deployment-Workflow

- Schnelle UI-/Template-Tests laufen auf `192.168.2.44` (`pitest`)
- Dauerhafte Staende werden per Commit nach `main` gepusht
- Produktive oder persistente Updates sollen anschliessend ueber das vorhandene Update-Script eingespielt werden

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
