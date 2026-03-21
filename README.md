# EVE PI Manager

Planetary Industry Dashboard für EVE Online — selbst gehostet, kein Cloud-Abo.

![Tech Stack](https://img.shields.io/badge/Python-3.11+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green) ![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-purple) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

| Feature | Beschreibung |
|---|---|
| **PI Kolonien** | Übersicht aller Planeten-Kolonien für Main + Alts |
| **ISK/Tag Berechnung** | Echtzeit-Preise via Fuzzwork Jita 4-4 |
| **Ablauf-Timer** | Nächster Extractor-Ablauf mit Farb-Warnsystem |
| **System Analyzer** | PI-Potential eines Systems analysieren (P0→P4 Ketten) |
| **Jita Marktpreise** | Live-Preise für alle PI-Produkte P1–P4 |
| **Multi-Charakter** | Main + beliebig viele Alts unter einem Account |
| **EVE SSO** | Login via EVE Online OAuth2, kein eigenes Passwort |
| **Admin Panel** | Account-Verwaltung |
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

### 2. Proxmox LXC erstellen (empfohlen)

| Einstellung | Wert |
|---|---|
| Template | Debian 12 (Bookworm) |
| CPU | 1–2 vCPUs |
| RAM | 512 MB |
| Disk | 4 GB |
| Netzwerk | Bridged, DHCP oder statisch |

---

### 3. Setup-Skript ausführen

```bash
# Repository klonen
git clone https://github.com/DrNightmareDev/PI_Manager.git /opt/eve-pi-manager
cd /opt/eve-pi-manager

# Setup starten (als root im LXC)
chmod +x scripts/setup_lxc.sh
bash scripts/setup_lxc.sh
```

Das Skript installiert und konfiguriert automatisch:
- Python 3, pip, venv
- PostgreSQL (mit zufälligem Passwort)
- Nginx (Reverse Proxy)
- systemd Service
- Alembic Datenbank-Migrationen

---

### 4. `.env` konfigurieren

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
```

---

### 5. Service starten

```bash
systemctl enable --now eve-pi-manager
```

Browser öffnen: `http://DEINE-IP`

Der **erste Account** erhält automatisch Admin-Rechte.

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
│   ├── models.py           # Account, Character, SSOState, MarketCache
│   ├── session.py          # Signed Cookie Sessions (itsdangerous)
│   ├── esi.py              # EVE ESI + SSO API
│   ├── sde.py              # Static Data Engine (EveRef – Schematics, Types)
│   ├── dependencies.py     # FastAPI Dependency Injection
│   ├── pi_data.py          # PI Produktionsdaten P0–P4
│   ├── pi_analyzer.py      # System-Analyzer Logik
│   ├── market.py           # Jita Marktdaten (Fuzzwork/Janice) mit Cache
│   ├── templates_env.py    # Jinja2 Templates + Custom Filter
│   ├── routers/
│   │   ├── auth.py         # SSO Login / Callback / Logout
│   │   ├── dashboard.py    # PI Kolonien Dashboard
│   │   ├── admin.py        # Admin Panel
│   │   ├── market.py       # Marktpreise
│   │   └── system.py       # System Analyzer
│   ├── templates/          # Jinja2 HTML Templates
│   └── static/             # CSS (EVE Dark Theme), JS, SVG
├── alembic/                # Datenbank-Migrationen
├── scripts/
│   ├── setup_lxc.sh        # Vollautomatisches LXC-Setup
│   ├── start.sh            # Entwicklungsserver
│   ├── eve-pi-manager.service   # systemd Unit
│   └── nginx-eve-pi.conf        # Nginx Konfiguration
├── data/                   # SDE-Daten (auto-generiert beim Start, nicht committet)
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
| Marktdaten | Fuzzwork / Janice API |
| Spieldaten | EveRef Static Data Export |
| Deployment | systemd + Nginx auf Proxmox LXC |

---

## API-Quellen

| Quelle | Verwendung |
|---|---|
| [EVE ESI](https://esi.evetech.net) | Charakter-Daten, PI-Kolonien, SSO |
| [Fuzzwork](https://market.fuzzwork.co.uk) | Jita Marktpreise |
| [EveRef SDE](https://data.everef.net/reference-data/) | Schematics, Item-Typen |

---

*EVE Online und alle zugehörigen Marken sind Eigentum von CCP Games.
Dieses Projekt steht nicht in Verbindung mit CCP Games.*
