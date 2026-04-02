# PlanetFlow — Hetzner Deployment Guide

## Voraussetzungen

- Hetzner VM (Ubuntu 22.04 oder 24.04 LTS) — empfohlen: CX22 (2 vCPU, 4 GB RAM)
- Domain `planetflow.app` mit DNS A-Record → IP des Servers
- GitHub-Repository: `DrNightmareDev/PlanetFlow.APP`

---

## 1. Server einrichten

```bash
# Als root auf dem frischen Server
bash <(curl -fsSL https://raw.githubusercontent.com/DrNightmareDev/PlanetFlow.APP/main/scripts/setup_hetzner.sh)
```

Das Script installiert:
- Docker + Docker Compose Plugin
- UFW Firewall (Port 22, 80, 443 offen)
- fail2ban
- Clont das Repository nach `/opt/planetflow`
- Legt `.env` aus `.env.example` an

---

## 2. .env konfigurieren

```bash
nano /opt/planetflow/.env
```

**Pflichtfelder:**

| Variable | Beschreibung |
|----------|-------------|
| `DB_PASSWORD` | PostgreSQL Passwort (beliebig, min. 16 Zeichen) |
| `SECRET_KEY` | Session-Signierung (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `EVE_CLIENT_ID` | EVE Developer App Client ID |
| `EVE_CLIENT_SECRET` | EVE Developer App Client Secret |
| `EVE_CALLBACK_URL` | `https://planetflow.app/auth/callback` |
| `RABBITMQ_PASS` | RabbitMQ Passwort (beliebig) |
| `COOKIE_SECURE` | `true` (Produktion mit HTTPS) |

---

## 3. TLS-Zertifikat holen (einmalig)

Vor dem ersten Start muss das Let's Encrypt Zertifikat existieren, da nginx es beim Start lädt.

```bash
cd /opt/planetflow

# Certbot-Volume erstellen und Zertifikat holen
docker run --rm \
  -v planetflow_certbot_www:/var/www/certbot \
  -v planetflow_certbot_conf:/etc/letsencrypt \
  -p 80:80 \
  certbot/certbot certonly \
  --standalone \
  --email admin@planetflow.app \
  --agree-tos \
  --no-eff-email \
  -d planetflow.app \
  -d www.planetflow.app
```

---

## 4. Stack starten

```bash
cd /opt/planetflow
docker compose up -d
docker compose logs -f app
```

---

## 5. Updates einspielen

```bash
cd /opt/planetflow
git pull
docker compose build --pull
docker compose up -d
```

---

## Wo liegt die .env?

```
/opt/planetflow/.env
```

Diese Datei wird **nicht** in Git eingecheckt (`.gitignore`). Sie muss auf dem Server manuell angelegt/gepflegt werden.

---

## Komponenten-Übersicht

| Service | Zweck |
|---------|-------|
| `db` | PostgreSQL 16 |
| `rabbitmq` | Message Broker |
| `app` | FastAPI + Gunicorn/Uvicorn (Port 8000 intern) |
| `celery_worker` | Hintergrund-Tasks (ESI, Skills, Billing) |
| `celery_wallet` | Wallet-Sync (eigene Queue) |
| `celery_beat` | Scheduler (periodische Tasks) |
| `nginx` | Reverse Proxy + TLS (Port 80/443) |
| `certbot` | Automatische Zertifikatserneuerung |

---

## Nützliche Befehle

```bash
# Logs live
docker compose logs -f

# Einzelnen Service neustarten
docker compose restart app

# Datenbank-Shell
docker compose exec db psql -U planetflow planetflow

# Celery-Tasks prüfen (mit Monitoring-Profil)
docker compose --profile monitoring up -d flower
# → http://SERVER_IP:5555  (localhost only, via SSH-Tunnel)
```
