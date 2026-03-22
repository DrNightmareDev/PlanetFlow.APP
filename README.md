# EVE PI Manager

Planetary Industry Dashboard für EVE Online — selbst gehostet, kein Cloud-Abo.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-purple)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Self-Hosted](https://img.shields.io/badge/Self--Hosted-✓-brightgreen)

---

## Features

| Feature | Beschreibung |
|---|---|
| **PI Kolonien** | Übersicht aller Planeten-Kolonien für Main + Alts |
| **ISK/Tag Berechnung** | Echtzeit-Preise via Fuzzwork Jita 4-4 |
| **Dashboard Cache** | Daten 15 min gecacht, Force-Refresh max. 1×/60 s (API-Schutz) |
| **Kolonie-Detail** | Fabrik-Übersicht pro Kolonie (Tier, Stück/Tag, ISK/Tag), fehlende Inputs, Lagerstand |
| **ISK/Tag Verlauf** | Tägliche Snapshots mit Chart.js Verlaufsgraph |
| **Ablauf-Timer** | Nächster Extractor-Ablauf mit Farb-Warnsystem, Filter für inaktive Kolonien |
| **Push-Benachrichtigungen** | Browser-Notifications wenn Extractoren ablaufen (120/60/30/10 Min.) |
| **Skyhook Inventar** | Skyhook-Bestand pro Planet direkt im Browser erfassen und bearbeiten; Füllstand-Anzeige (35.000 m³), ISK-Wert live mit Hover-Aufschlüsselung, Verlauf der letzten 3 Einträge, Undo-Button, Edit-Lock (immer nur ein Skyhook gleichzeitig bearbeitbar), Filter + sortierbare Tabelle |
| **Pagination** | Alle Tabellen mit konfigurierbarer Seitengröße (6/15/25/100/Alle) |
| **PI Chain Planner** | Vollständige P0–P4 Produktionsketten mit interaktivem SVG-Graph (PLANETS → RAW → P1 → … → P4) |
| **Planeten-Filter** | Im Graphen Planeten anklicken → nicht produzierbare Knoten ausgegraut |
| **Planner System-Check** | System direkt im Planner prüfen — zeigt ob alle P0-Ressourcen verfügbar sind |
| **System Analyzer** | PI-Potential eines Systems analysieren (P0→P4 Ketten, Preise, Trends) |
| **System Vergleich** | Bis zu 4 Systeme nebeneinander vergleichen (localStorage) |
| **System-Favoriten** | Systeme im Analyzer als Favorit speichern, Schnellzugriff per Pill (localStorage) |
| **Produkt-Favoriten** | PI-Produkte als Favorit speichern, Schnellauswahl im Planner |
| **Corporation-Übersicht** | Alle Kolonien der Corporation auf einen Blick (CEO/Admin); sortierbare Tabelle, Tier- und Lager-Hover-Cards, ISK/Tag |
| **Corporation Force-Load** | Owner kann alle Accounts einer Corporation per Modal neu laden (nur noch nicht gecachte werden angezeigt und geladen, mit Live-Fortschritt) |
| **Corporation Switcher** | Owner kann zwischen allen Corps auf der Instanz wechseln |
| **Gesamt-Übersicht** | Alle Kolonien aller Accounts aggregiert (Owner/Admin), filterbar |
| **Multi-Charakter** | Main + beliebig viele Alts unter einem Account |
| **EVE SSO** | Login via EVE Online OAuth2, kein eigenes Passwort |
| **Admin Panel** | Account-Verwaltung, Charakter-Suche, Admin-Filter |
| **Zugangspolitik** | Besitzer kann System offen, per Allowlist oder Blocklist betreiben; wird bei jedem Login geprüft (auch für bestehende Accounts) |
| **Owner-System** | Erster Account = Besitzer, stealth (nur für den Besitzer selbst sichtbar), geschützt vor Löschung/Entfernung |
| **Jita Marktpreise** | Live-Preise P1–P4, Tier-Filter, Sortierung, 24h/7T/30T Trends |
| **Light / Dark Mode** | Theme-Toggle in der Navbar, Einstellung per localStorage gespeichert |
| **EveRef SDE** | Statische Spieldaten lokal (Schematics, Types) – kein ESI-Overhead |

---

## Schnellstart

### 1. EVE App registrieren

Unter [developers.eveonline.com](https://developers.eveonline.com) eine neue App erstellen:

- **Callback URL:** `http://DEINE-IP/auth/callback`
- **Scopes:**
  - `esi-planets.manage_planets.v1`
  - `esi-planets.read_customs_offices.v1`
  - `esi-location.read_location.v1`
  - `esi-search.search_structures.v1`

Client ID und Client Secret notieren.

---

### 2. Installation wählen

Es gibt zwei Wege: **Docker Compose** (einfacher, überall lauffähig) oder **Proxmox LXC** (nativ, ressourcenschonend).

---

#### Option A: Docker Compose

**Voraussetzungen:** Docker + Docker Compose Plugin

```bash
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager

cp .env.example .env
nano .env   # EVE_CLIENT_ID, EVE_CLIENT_SECRET, EVE_CALLBACK_URL, SECRET_KEY, DB_PASSWORD eintragen

docker compose up -d
```

Browser öffnen: `http://DEINE-IP`

```bash
# Logs
docker compose logs -f app

# Stoppen
docker compose down

# Update
git pull && docker compose up -d --build
```

> **Hinweis:** `EVE_CALLBACK_URL` muss auf den Docker-Host zeigen, z.B. `http://192.168.2.44/auth/callback`.
> `DATABASE_URL` in `.env` wird vom Compose-File automatisch auf den internen `db`-Service gesetzt — nur `DB_PASSWORD` ist nötig.

---

#### Option B: Proxmox LXC erstellen

| Einstellung | Wert |
|---|---|
| Template | Debian 12 (Bookworm) |
| CPU | 1–2 vCPUs |
| RAM | 512 MB |
| Disk | 4 GB |
| Netzwerk | Bridged, DHCP oder statisch |

---

### 3. LXC Setup-Skript ausführen

```bash
# .env vorbereiten (Credentials vor dem Setup eintragen!)
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager
cp .env.example .env
nano .env   # EVE_CLIENT_ID, EVE_CLIENT_SECRET, EVE_CALLBACK_URL, DB_PASSWORD, SECRET_KEY eintragen

# Setup als root im LXC ausführen
chmod +x scripts/setup_lxc.sh
bash scripts/setup_lxc.sh
```

Das Skript installiert und konfiguriert automatisch:
- Python 3, pip, venv
- PostgreSQL (Passwort aus `.env` oder zufällig generiert)
- Nginx (Reverse Proxy)
- systemd Service
- Alembic Datenbank-Migrationen

---

### 4. `.env` konfigurieren (LXC)

```bash
cp .env.example .env
nano .env
```

```env
EVE_CLIENT_ID=dein_client_id
EVE_CLIENT_SECRET=dein_client_secret
EVE_CALLBACK_URL=http://DEINE-IP/auth/callback
SECRET_KEY=min_32_zeichen_zufaelliger_schluessel
DATABASE_URL=postgresql://evepi:PASSWORT@localhost/evepi
DB_PASSWORD=PASSWORT
```

---

### 5. Service starten (LXC)

```bash
systemctl enable --now eve-pi-manager
```

Browser öffnen: `http://DEINE-IP`

Der **erste Account** erhält automatisch Admin- und Besitzer-Rechte.

---

## Update (LXC)

```bash
bash ~/PI_Manager/scripts/update_lxc.sh
```

Das Script holt automatisch die neueste Version, bewahrt `.env` und alle Credentials, aktualisiert Abhängigkeiten und Migrationen und startet den Service neu.

---

## Service-Verwaltung

```bash
systemctl status eve-pi-manager       # Status
journalctl -u eve-pi-manager -f       # Live-Logs
systemctl restart eve-pi-manager      # Neustart
```

---

## Datenbank

```bash
# Direkt verbinden
sudo -u postgres psql -d evepi

# Neue Migration (Entwicklung)
cd /opt/eve-pi-manager
./venv/bin/alembic revision --autogenerate -m "Beschreibung"
./venv/bin/alembic upgrade head
```

---

## Lokale Entwicklung

```bash
# Voraussetzungen: Python 3.11+, PostgreSQL

git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager

cp .env.example .env
# .env ausfüllen

python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

---

## Projektstruktur

```
eve-pi-manager/
├── app/
│   ├── main.py             # FastAPI App, Startup-Logik, APScheduler
│   ├── config.py           # Pydantic-Settings, .env
│   ├── database.py         # SQLAlchemy Engine + Session
│   ├── models.py           # ORM-Modelle (Account, Character, PI, Policy …)
│   ├── session.py          # Signed Cookie Sessions (itsdangerous)
│   ├── esi.py              # EVE ESI + SSO API
│   ├── sde.py              # Static Data Engine (EveRef – Schematics, Types)
│   ├── dependencies.py     # FastAPI Dependency Injection
│   ├── pi_data.py          # PI Produktionsdaten P0–P4
│   ├── pi_analyzer.py      # System-Analyzer Logik
│   ├── market.py           # Jita Marktdaten (Fuzzwork) mit Cache + Trends
│   ├── templates_env.py    # Jinja2 Templates + Custom Filter
│   ├── routers/
│   │   ├── auth.py         # SSO Login / Callback / Logout / Owner-System / Zugangspolitik
│   │   ├── dashboard.py    # PI Kolonien Dashboard (Cache, ISK-Snapshots, Corp-Übersicht)
│   │   ├── admin.py        # Admin Panel (Account-Verwaltung, Zugangspolitik)
│   │   ├── skyhook.py      # Skyhook Inventar (Bestand, Verlauf, ISK-Wert)
│   │   ├── market.py       # Marktpreise + Trends + Admin-Refresh
│   │   ├── system.py       # System Analyzer + System-Vergleich
│   │   └── planner.py      # PI Chain Planner + Favoriten
│   ├── templates/          # Jinja2 HTML Templates
│   └── static/             # CSS (EVE Dark Theme), JS, SVG, Service Worker
├── alembic/                # Datenbank-Migrationen
├── docker/
│   ├── entrypoint.sh       # Docker Entrypoint (DB-Wait → Migrations → Start)
│   └── nginx.conf          # Nginx Konfiguration für Docker
├── scripts/
│   ├── setup_lxc.sh        # Vollautomatisches LXC-Setup
│   ├── update_lxc.sh       # Update ohne .env-Überschreibung
│   ├── eve-pi-manager.service   # systemd Unit
│   └── nginx-eve-pi.conf        # Nginx Konfiguration für LXC
├── data/                   # SDE-Daten (auto-generiert beim Start, nicht committet)
├── LICENSE
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── alembic.ini
├── .env.example
└── .gitignore
```

---

## Static Data Engine (SDE)

Beim Start lädt der PI Manager automatisch die aktuellen Spieldaten von [EveRef](https://data.everef.net/reference-data/) herunter:

- **Schematics** – alle PI-Produktionsrezepte (Inputs, Outputs, Mengen, Zykluszeiten)
- **Types** – alle EVE Item-Namen und IDs
- **Version** – gespeichert in `data/meta.json` (`build_time`)
- **Auto-Update** – wöchentlich, beim nächsten App-Start

```
data/
├── meta.json         # build_time der aktuellen Version
├── schematics.json   # ~68 PI Schematics
└── types.json        # ~51.600 Item-Types
```

Die ESI API wird weiterhin für **Echtzeit-Daten** genutzt (Charakter-Kolonien, Marktpreise, Login).

---

## Sicherheitshinweise

- `.env` niemals committen — enthält Secrets
- `SECRET_KEY` muss zufällig und mindestens 32 Zeichen lang sein
- Access Tokens werden in der Datenbank gespeichert → DB absichern
- CSRF-Schutz via SSO State (einmalig, 1h TTL)
- Sessions sind signiert (itsdangerous), 30 Tage gültig
- Nginx als Reverse Proxy (kein direkter Uvicorn-Zugriff von außen)

---

## Tech Stack

| Komponente | Technologie |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Datenbank | PostgreSQL + SQLAlchemy 2.0 |
| Migrationen | Alembic |
| Frontend | Jinja2, Bootstrap 5.3 Dark Theme |
| Auth | EVE Online SSO (OAuth2) |
| Sessions | itsdangerous (signed cookies) |
| Marktdaten | Fuzzwork API |
| Spieldaten | EveRef Static Data Export |
| Scheduler | APScheduler (Marktpreise alle 15 min) |
| Notifications | Web Notifications API + Service Worker |
| Deployment | Docker Compose **oder** systemd + Nginx auf Proxmox LXC |

---

## API-Quellen

| Quelle | Verwendung |
|---|---|
| [EVE ESI](https://esi.evetech.net) | Charakter-Daten, PI-Kolonien, SSO |
| [Fuzzwork](https://market.fuzzwork.co.uk) | Jita Marktpreise |
| [EveRef SDE](https://data.everef.net/reference-data/) | Schematics, Item-Typen |

---

## Ideen & Roadmap

Mögliche zukünftige Features — Pull Requests willkommen:

| Idee | Beschreibung |
|---|---|
| **Discord / Telegram Webhook** | Extractor-Ablauf-Notifications via Webhook statt Browser |
| **Hauling Kalkulator** | Transportkosten Planet → Jita in ISK/Tag einrechnen |
| **PI Setup Vorlagen** | Kolonien-Konfigurationen speichern und teilen |
| **Preisalarm** | Notification wenn PI-Produkt Preis-Schwellenwert über-/unterschreitet |
| **Reprocess-Rechner** | Wie viele P0 brauche ich für X Einheiten P4? |
| **PWA / Mobile** | Als App installierbar (Service Worker bereits vorhanden) |
| **Mehrsprachigkeit** | English / Deutsch Umschaltung |

---

## Support / ISK-Spende

Falls dir der PI Manager nützlich ist und du die Entwicklung unterstützen möchtest:

> **Schick eine ISK-Spende in EVE Online an `DrNightmare`** — jeder Betrag ist willkommen, danke!

---

## Lizenz

Dieses Projekt steht unter der **MIT-Lizenz** — siehe [LICENSE](LICENSE).

---

## Rechtliche Hinweise

EVE Online, das EVE-Logo, EVE und alle zugehörigen Logos und Designs sind eingetragene Marken oder Marken von CCP ehf. Alle EVE-In-Game-Screenshots und Bilder sind Eigentum von CCP ehf. Alle Rechte vorbehalten. CCP ehf. ist weder mit diesem Projekt verbunden noch hat CCP ehf. dieses Projekt genehmigt oder unterstützt.

*EVE PI Manager* ist ein unabhängiges Drittanbieter-Tool und steht in keiner Verbindung zu CCP Games.
