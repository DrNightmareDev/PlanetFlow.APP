# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

Self-hosted Planetary Industry manager for EVE Online.

If this project helps you, Ingame-ISK donations to `DrNightmare` are welcome.

## Highlights

- Dashboard, Skyhooks, Characters, Corporation, Jita Market, System Analyzer, Compare, System Mix, PI Chain Planner, and Fittings
- **Celery + RabbitMQ** background refresh — dashboard always loads instantly, ESI fetched in background every 5 min
- **ETag caching** — ~60–70% fewer ESI calls after first run via HTTP 304
- **Live expiry countdown** — colony expiry timers update every minute in the browser without page reload
- **Pagination** — 50 rows/page default, configurable; handles thousands of colonies without blocking the browser
- **Discord / Webhook alerts** — server-side expiry notifications per account, configurable threshold + cooldown; Discord rate-limiting handled automatically
- **Token status overview** — banner + per-character ESI error tracking; auto-retry after 24 h; no false positives
- **Manager panel** — ESI error reset, colony cache reload, access policy (allow/blocklist), and GUI translation editing
- **CSV export** — download colony list directly from dashboard
- **Mobile-responsive** — compact table on small screens
- PI Templates with to-scale canvas rendering and community imports (GitHub)
- DB-backed caches for market prices, dashboard values, skyhook values, ETag responses, GUI translations, and static planet details
- Optional nginx, PgBouncer, Flower task monitor, and Sentry error tracking
- GUI languages: German, English, and Simplified Chinese
- Linux, Docker Compose, and native Windows setup/update/upgrade scripts

## Full documentation

- [Deutsch](README.de.md)
- [English](README.en.md)
- [简体中文](README.zh-Hans.md)

## Quick start

```bash
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager
cp .env.example .env
# Fill in .env, then:
docker compose up -d
```

## Upgrade from older version (native Linux)

```bash
sudo bash scripts/upgrade_to_latest.sh
```

Handles RabbitMQ install, new `.env` keys, uvicorn → gunicorn migration, Celery systemd units, pip deps, and DB migrations automatically.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/setup_linux.sh` | Fresh native Linux install |
| `scripts/upgrade_to_latest.sh` | Upgrade any version to latest (native Linux) |
| `scripts/update_linux.sh` | Regular update (native or `--compose`) |
| `scripts/update_compose.sh` | Regular update for Docker Compose installs |
| `scripts/update_windows.ps1` | Update on Windows (native or `-Compose`) |

## Health check

```
GET /health
→ {"status": "ok", "database": "ok", "rabbitmq": "ok"}
```

## CCP Notice

EVE Online and all related logos and designs are trademarks or registered trademarks of CCP ehf. This project is not affiliated with, endorsed by, or connected to CCP ehf.

## License

MIT. See [LICENSE](LICENSE).
