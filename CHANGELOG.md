# Changelog

## 1.0.0 — 2026-04-06

### KillIntel — major overhaul

- **Streaming analysis** — pilot cards appear one by one as they load (NDJSON stream), no more waiting for all results
- **Two-phase loading** — kill stats appear instantly in Phase 1; ship details fill in during Phase 2 without blocking the page
- **Parallel Phase 1 fetches** — all stat cards load simultaneously instead of sequentially
- **Time window filter** — analyse the last 24h, 7d, 14d, or all time
- **Smart cache tiers** — per-pilot cache with green/orange/red freshness indicators; "Use Cache" button to skip re-fetching
- **Shareable URLs** — browser URL stays in sync with the current search; paste it to a fleet member and they open the exact same results
- **Copy zKillboard URL** — one-click clipboard button on every pilot card
- **Ship display improvements** — zKill link, loss/kill colour coding, ship name visible during analyse, cache legend tooltip
- **Stop button** — cancel an in-progress analysis at any time
- **Rate limiting** — 30-second cooldown only applies when the time window changes, not on every run
- **EVE Local Chat paste** — paste directly from EVE's local chat window (handles showinfo links automatically)
- **Dash-separated name support** — handles names pasted with dashes as separators
- **Type name resolution fixed** — modules and ships now show real names on first analyse, not numeric IDs
- **Name resolution via ESI** — switched to direct ESI `/universe/names/` POST for reliable lookups

### Inventory — new features

- **Bulk import modal** — copy a multi-item list from inside EVE (Ctrl+C), paste it into the new import dialog, review quantities and optionally enter a unit cost per item, then submit all in one go
- **Edit unit cost on existing lots** — pencil icon on each transaction row in the Stock Transactions table lets you update the unit cost after the fact; weighted average cost recalculates automatically
- **Negative quantity guard** — quantities of zero or less are now rejected with a clear error message

### Security — hardening

- CSRF protection added to all remaining state-mutating endpoints: skyhook, hauling, intel preferences, pi_templates (upload/delete/admin-seed), planner favorites, killintel analyse, and all admin JSON endpoints
- CSRF cookie is now guaranteed to be set on every page load, including pages without forms
- Input validation tightened: negative quantities and negative unit costs rejected at the API layer
- Exception messages no longer leak raw Python errors to the browser
- Webhook URL validation uses `urlparse` instead of string prefix checks (prevents subdomain bypass)
- `X-Real-IP` used for client IP instead of spoofable `X-Forwarded-For`
- Docker Compose removed all credential fallback defaults — startup fails loudly without a `.env`
- Dotlan bridge URL validated against path prefix `/bridges` in addition to hostname

### Local setup — much simpler

- **`NGINX_MODE=local`** — new mode that skips TLS certificate handling entirely; ideal for running on your own PC
- **Configurable host ports** — `NGINX_HTTP_PORT` and `NGINX_HTTPS_PORT` in `.env` let you avoid conflicts with other software (e.g. IIS on port 80)
- Default local config now uses `http://localhost:8080` — no more fighting with port 80
- `.env.example` cleaned up: pure ASCII (no Unicode box-drawing characters), all required fields clearly marked, local vs server values documented side by side

### PI Templates

- **Copy JSON button** on the template detail page — copies the raw layout JSON to clipboard in one click
- **Download JSON button** — saves the layout as a `.json` file

### Other features

- **Sov Timers page** — DB-backed sovereignty timer list, refreshed by Celery every 15 minutes
- **Combat nav group** — ESS and Sov Timers grouped under a new Combat section in the navigation
- **Billing system** — global on/off switch (default off); seed default subscription plans; cookie consent banner
- **Footer** — GitHub and planetflow.app links added to footer and help modal

### Documentation

- Full migration guide from EVE PI Manager added to all READMEs (EN/DE/ZH) — covers what carries over, what needs re-entry, and a config comparison table
- READMEs restructured: local setup as the primary path, server setup secondary
- Troubleshooting section with the most common first-run problems
- Environment variable reference table with descriptions and example values for every setting

---

## 0.9.0 and earlier

See git history.
