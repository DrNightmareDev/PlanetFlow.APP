# Release Notes

## v0.4.0 — 2026-03-30

### New Features

**Fittings Compare**
- New `/fittings` page: side-by-side ESI fitting comparison for all characters
- Import fittings as EFT text blocks or compare saved ESI fits directly
- Automatic slot inference for imported fittings; highlights differences across hulls
- Accessible to all accounts (no admin required); scope warning collapsed when not authorized

**Live Expiry Countdown**
- Colony expiry timers now count down every 60 seconds in the browser without a page reload
- Timers automatically switch between `text-success` / `text-warning` / `text-danger` as time changes

**Dashboard Improvements**
- Pagination: 50 rows/page by default, configurable up to All; all-page selection is remembered across filter changes
- CSV export now includes all colony fields
- Token error banner is dismissible per session and only reappears when the affected character set changes
- Token warning no longer triggers false positives for characters that have already successfully synced
- Colony cache is never overwritten with a zero-colony result from a failed ESI call

**Webhook Alerts**
- Discord/Webhook alert configuration moved to the character dropdown (removed the separate "Werkzeuge" nav section)
- HTTP errors from webhook calls now show a friendly message instead of a raw exception
- Switched from `urllib` to `requests` for all webhook HTTP calls
- Discord 429 rate-limit responses are handled: waits `Retry-After` seconds (capped at 30) before continuing

**Navigation**
- Planner and analysis pages grouped into "Planer" and "Analyse" dropdowns for a cleaner navbar

**Manager Panel**
- ESI error reset: characters with `esi_consecutive_errors >= 3` show a red badge; the ↺ button resets the counter and `colony_sync_issue` flag immediately without waiting 24h
- Manager page load no longer issues per-account ESI calls; colony counts come from a single aggregation query
- Colony cache can be force-reloaded for any account directly from the Manager

### Security

- **Startup validation**: App refuses to start if `SECRET_KEY`, `EVE_CLIENT_ID`, or `EVE_CLIENT_SECRET` are left at their defaults or empty
- **Secure cookies**: Session cookies now set `secure=True` automatically when `DEBUG=false`; `httponly` and `samesite=lax` on all cookie operations including logout
- **SSRF protection**: Webhook URLs validated against `discord.com/api/webhooks/` allowlist server-side
- **ESI error budget**: `X-ESI-Error-Limit-Remain` header checked after every ESI call; pauses 10s when below 20 remaining errors
- **Information disclosure**: Raw exception messages removed from all HTTP error responses across auth, system, dashboard, market, and admin routers — errors are logged server-side only
- **Product name injection**: `toggle_favorite` endpoint validates product names against a known PI product set
- **Docker**: App container now runs as unprivileged user `appuser` (uid 1000)
- **Docker healthcheck**: `app` container health verified via `curl /health` every 30s

### Performance & Reliability

- **DB indexes**: Three new indexes via Alembic migrations (`ix_characters_account_id`, `ix_characters_corporation_id`, `ix_skyhook_entries_account_planet`, `ix_sso_states_created_at`) — eliminates full-table scans on hot paths
- **ETag cache (thread safety)**: `PlanetEsiCache` write now wrapped in a per-planet DB upsert to prevent race conditions under concurrent Celery workers
- **Corp load lock sweep**: `_get_corp_load_lock` sweeps all expired entries on every call instead of only the requested corp — prevents unbounded dict growth
- **Planet info cache TTL**: `_planet_info_cache` entries now carry a timestamp and expire after 24h, preventing stale data from persisting indefinitely
- **System planet cache LRU**: `_system_planet_cache` capped at 500 entries with FIFO eviction — prevents unbounded memory growth on large instances
- **Market refresh lock**: `can_force_market_refresh` / `record_force_refresh` wrapped with `threading.Lock()` — safe under concurrent gunicorn workers
- **DB connection pool**: Pool size, overflow, and recycle interval now configurable via `DB_POOL_SIZE`, `DB_POOL_OVERFLOW`, `DB_POOL_RECYCLE` env vars
- **ESI token retry**: Token refresh retries up to 3 times with exponential backoff (2s, 4s); 4xx errors (401/403) fail immediately without retrying
- **Auto-reset on success**: After a successful background colony sync, `esi_consecutive_errors` is reset to 0 for all synced characters — prevents stale error counts blocking healthy characters
- **Dashboard colony table**: Optimized rendering for large colony lists

### Bug Fixes

- `refresh-status` endpoint now uses the DB-persisted cache timestamp and the `since=` query parameter — previously used in-process state that was lost on gunicorn worker restart
- Fixed double DB session in the `/` root route
- Fixed `WEB_WORKERS` not resolving from `.env` at systemd unit generation time
- Default `WEB_WORKERS` lowered from 4 to 2 to prevent OOM on 2 GB servers

### Upgrade Notes

Run database migrations after pulling:

```bash
# Docker Compose
docker compose exec app alembic upgrade head

# Native Linux
cd /opt/planetflow && alembic upgrade head
```

No breaking changes to `.env`. New optional env vars:

| Variable | Default | Description |
|---|---|---|
| `DB_POOL_SIZE` | `5` | SQLAlchemy connection pool size |
| `DB_POOL_OVERFLOW` | `10` | Max overflow connections above pool size |
| `DB_POOL_RECYCLE` | `3600` | Connection recycle interval in seconds |

---

## v0.3.0 — 2026-03-24

- Celery + RabbitMQ background refresh replacing the in-process APScheduler loop
- PI Surface Templates with to-scale canvas rendering and GitHub community import (DalShooth, TheLegi0n-NBI)
- Corporation overview page with async loading for uncached accounts
- System Analyzer, System Mix, Compare, and PI Chain Planner pages
- Skyhook inventory with history and DB value cache
- PI skills per character in card and list views
- Manager panel with account management, access policy (allow/blocklist), and GUI translation editing
- DB-backed ETag cache for planet detail ESI calls (~60–70% fewer requests after first run)
- Colony Assignment Planner and performance optimizations
- Native Linux install/upgrade/update scripts + Docker Compose profiles (nginx, pgbouncer, monitoring)

## v0.2 — initial public release

- Multi-account PI dashboard with Main + Alt support
- Basic colony overview with ISK/day and expiry timers
- Jita market prices (cached)
- EVE SSO login with session cookies
- PostgreSQL + SQLAlchemy + Alembic schema management
