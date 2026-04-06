# PlanetFlow — Deutsch

[Deutsch](README.de.md) | [English](README.en.md) | [ZH-CN](README.zh-Hans.md)

Selbst gehostete Planetary-Industry-Plattform für EVE Online.

> **[planetflow.app](https://planetflow.app)** — gehostete Version ausprobieren oder selbst hosten.

Wenn dir das Projekt hilft, freue ich mich über Ingame-ISK an `DrNightmare`.

---

## Umstieg von EVE PI Manager?

EVE PI Manager wurde archiviert. PlanetFlow ist der Nachfolger — alle Funktionen wurden portiert und erweitert.

**Deine Daten werden nicht automatisch übernommen.** Beide Apps verwenden separate Datenbanken. Die Migration ist aber unkompliziert:

### Schritt 1 — PlanetFlow frisch aufsetzen

Folge der [Lokalen Installation](#lokale-installation-eigener-pc-keine-domain-nötig) oder [Server-Installation](#server-installation-mit-domain--https) weiter unten. PlanetFlow ist eine Neuinstallation — du brauchst nur dein EVE-SSO-Login.

### Schritt 2 — EVE-Entwickleranwendung neu registrieren (oder bestehende wiederverwenden)

- Du kannst deine bestehende EVE-Entwickleranwendung von EVE PI Manager wiederverwenden — ändere einfach die **Callback URL** auf PlanetFlow.
- Oder erstelle eine neue App auf [https://developers.eveonline.com](https://developers.eveonline.com). Die benötigten Scopes sind identisch.

### Schritt 3 — Einloggen und Charaktere neu hinzufügen

PlanetFlow nutzt denselben EVE-SSO-Ablauf. Einloggen, zu Characters gehen und jeden Charakter neu autorisieren. ESI-Daten werden automatisch im Hintergrund synchronisiert — Kolonien, Ablaufzeiten und Planetendaten erscheinen innerhalb weniger Minuten.

### Schritt 4 — Manuelle Daten neu eingeben

Daten, die lokal in EVE PI Manager gespeichert waren und nicht aus ESI kommen, müssen manuell neu eingegeben werden:
- **Inventory-Bestände** — über die Inventory-Seite neu hinzufügen
- **Hauling-Routen / Bridge-Verbindungen** — im Hauling-Bereich neu konfigurieren
- **Skyhook-Einträge** — in Skyhooks neu eintragen
- **PI-Templates** — in PI Templates neu hochladen

### Schritt 5 — EVE PI Manager abschalten

Sobald PlanetFlow läuft und synchronisiert ist:
```bash
# im alten eve-pi-manager-Verzeichnis
docker compose down
```

Das alte Datenbankvolume kann als Backup behalten oder vollständig entfernt werden:
```bash
docker compose down -v   # entfernt auch Volumes — nicht rückgängig zu machen
```

### Wichtigste Unterschiede zu EVE PI Manager

| | EVE PI Manager | PlanetFlow |
|---|---|---|
| HTTPS / TLS | Optionales nginx-Profil | Eingebaut (Let's Encrypt oder Proxy-Modus) |
| Billing & Zugriffskontrolle | Nicht vorhanden | Eingebaut |
| Admin-Bereich | `/manager` | `/admin` |
| Hintergrundworker | Celery + APScheduler-Fallback | Nur Celery (RabbitMQ erforderlich) |
| Konfigurationsschlüssel | `CELERY_BROKER_URL` | `RABBITMQ_USER` / `RABBITMQ_PASS` |
| Lokaler HTTP-Modus | `COOKIE_SECURE=false` | `COOKIE_SECURE=false` + `NGINX_MODE=local` |

---

## Voraussetzungen

Du brauchst nur **Docker Desktop**. Das war's.

- Windows / Mac: [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
- Linux: `docker` + `docker compose` über den Paketmanager installieren

---

## Schritt 1 — EVE-Entwickleranwendung erstellen

Du brauchst einen EVE-Online-API-Key.

1. Öffne [https://developers.eveonline.com](https://developers.eveonline.com) und logge dich ein
2. Klicke auf **Create New Application**
3. Fülle aus:
   - **Name:** beliebig (z. B. `Mein PlanetFlow`)
   - **Connection Type:** `Authentication & API Access`
   - **Callback URL:**
     - Lokal: `http://localhost:8080/auth/callback` (oder dein eigener `NGINX_HTTP_PORT`)
     - Server mit Domain: `https://deinedomain.de/auth/callback`
   - **Scopes** — alle diese hinzufügen:
     ```
     esi-planets.manage_planets.v1
     esi-planets.read_customs_offices.v1
     esi-location.read_location.v1
     esi-characters.read_corporation_roles.v1
     esi-skills.read_skills.v1
     esi-fittings.read_fittings.v1
     ```
4. Speichern und **Client ID** sowie **Client Secret** kopieren

---

## Lokale Installation (eigener PC, keine Domain nötig)

PlanetFlow läuft auf deinem Computer unter `http://localhost`.  
Keine Domain, kein TLS-Zertifikat, keine nginx-Konfiguration nötig.

### 1. Projekt herunterladen

```bash
git clone https://github.com/your-org/planetflow.app.git
cd planetflow.app
```

### 2. Konfigurationsdatei anlegen

```bash
cp .env.example .env
```

Öffne `.env` in einem Texteditor und trage diese Werte ein:

```env
# Passwort für die interne Datenbank — beliebig wählen
DB_PASSWORD=mein_lokales_passwort

# Deine EVE-Charakter-ID (findest du auf https://evewho.com)
EVE_OWNER_CHARACTER_ID=123456789

# Aus deiner EVE-Entwickleranwendung (Schritt 1)
EVE_CLIENT_ID=dein_client_id
EVE_CLIENT_SECRET=dein_client_secret
EVE_CALLBACK_URL=http://localhost:8080/auth/callback

# Geheimen Schlüssel generieren (muss lower/UPPER/Zahl/Sonderzeichen enthalten):
# python3 -c "import secrets,string; l=string.ascii_lowercase; u=string.ascii_uppercase; d=string.digits; s='!@#$%^&*()-_=+[]{}:,.?'; a=l+u+d+s; p=[secrets.choice(l),secrets.choice(u),secrets.choice(d),secrets.choice(s)]+[secrets.choice(a) for _ in range(44)]; secrets.SystemRandom().shuffle(p); print(''.join(p))"
SECRET_KEY=deinen_generierten_schluessel_hier_eintragen

# Passwort für die interne Message Queue — beliebig wählen
RABBITMQ_USER=planetflow
RABBITMQ_PASS=mein_lokales_rabbit_passwort

# WICHTIG für lokal: muss false sein (kein HTTPS lokal)
COOKIE_SECURE=false

# WICHTIG für lokal: local-Modus — kein TLS-Zertifikat nötig
NGINX_MODE=local

# Optional: lokale Host-Ports (Standardwerte)
NGINX_HTTP_PORT=8080
NGINX_HTTPS_PORT=8443
```

Alles andere kann so bleiben wie es ist.

### 3. Starten

```bash
docker compose up -d
```

Docker lädt und baut alles automatisch. Beim ersten Mal dauert das ein paar Minuten.

### 4. Im Browser öffnen

[http://localhost:8080](http://localhost:8080)

Mit EVE-SSO einloggen. Der erste Account, der sich anmeldet, wird automatisch Owner (Admin).

### 5. Stoppen

```bash
docker compose down
```

---

## Server-Installation (mit Domain + HTTPS)

### 1. Server vorbereiten

Auf einem frischen Ubuntu-Server (getestet auf Hetzner):

```bash
bash scripts/setup_hetzner.sh
```

Das installiert Docker und alle Abhängigkeiten.

### 2. Konfigurationsdatei anlegen

```bash
cp .env.example .env
nano .env
```

Pflichtfelder:

```env
DB_PASSWORD=sicheres_datenbankpasswort
EVE_OWNER_CHARACTER_ID=123456789
EVE_CLIENT_ID=dein_client_id
EVE_CLIENT_SECRET=dein_client_secret
EVE_CALLBACK_URL=https://deinedomain.de/auth/callback
SECRET_KEY=deinen_generierten_schluessel
RABBITMQ_PASS=sicheres_rabbit_passwort
COOKIE_SECURE=true
NGINX_MODE=https
NGINX_HTTP_PORT=80
NGINX_HTTPS_PORT=443
```

### 3. Starten (mit automatischem TLS)

```bash
bash scripts/start.sh
```

Dieses Skript prüft die Konfiguration, holt ein Let's-Encrypt-Zertifikat und startet alle Dienste.

### 4. Bestehendes Deployment aktualisieren

```bash
bash scripts/update.sh
```

---

## Adminrechte vergeben

Nach dem ersten Login:

```bash
docker compose exec app python scripts/add_administrator.py
```

---

## Nützliche Befehle

```bash
# Logs anzeigen
docker compose logs -f app
docker compose logs -f celery_worker

# Status aller Container
docker compose ps

# Alles neu starten
docker compose restart

# Container stoppen (Daten bleiben erhalten)
docker compose down
```

---

## Fehlerbehebung

**Login funktioniert nicht / Callback-Fehler**
- `EVE_CALLBACK_URL` in `.env` muss exakt mit der Callback-URL in der EVE-Entwickleranwendung übereinstimmen
- Lokal (Standard): `http://localhost:8080/auth/callback` (oder passend zu deinem `NGINX_HTTP_PORT`)
- Lokal: `COOKIE_SECURE` muss `false` sein

**Seite lädt, zeigt aber keine Daten**
- App-Logs prüfen: `docker compose logs -f app`
- Worker-Logs prüfen: `docker compose logs -f celery_worker`

**Port 80 bereits belegt**
- Ein anderes Programm (IIS, anderer Webserver) nutzt Port 80.
- Setze `NGINX_HTTP_PORT` in `.env` z. B. auf `8080` und passe `EVE_CALLBACK_URL` entsprechend an.

**"Connection refused" auf lokaler URL**
- 30–60 Sekunden nach `docker compose up -d` warten — die App braucht etwas Zeit zum Starten
- Prüfen: `docker compose ps` — alle Dienste sollten `healthy` oder `running` anzeigen

---

## Dienste im Überblick

| Dienst | Aufgabe |
|---|---|
| `db` | PostgreSQL-Datenbank |
| `rabbitmq` | Message Queue für Hintergrundjobs |
| `app` | Die Webanwendung |
| `celery_worker` | Hintergrundjob-Worker (ESI-Sync usw.) |
| `celery_beat` | Job-Scheduler (zeitgesteuerte Aufgaben) |
| `nginx` | Webserver / Reverse Proxy |
| `certbot` | Automatische TLS-Zertifikatserneuerung (nur Server) |

---

## Funktionsumfang

- Dashboard mit Koloniestatus, Ablaufzeiten, ISK/Tag, CSV-Export
- Characters, Corporation, Inventory, Hauling, Intel, Killboard, Skyhooks, Templates
- PI Chain Planner, Colony Assignment Planner, System Analyzer, Compare, Fittings
- Billing-Seiten und seitenbasierte Zugriffskontrolle
- Deutsch, Englisch und vereinfachtes Chinesisch

## Tech Stack

- FastAPI + PostgreSQL + Celery + RabbitMQ + nginx
- Bootstrap 5 · Deployment via Docker Compose

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
