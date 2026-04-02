from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.database import engine, SessionLocal
from app.i18n import bootstrap_pi_type_translations, bootstrap_static_planets, bootstrap_static_stargates, bootstrap_translations
from app.models import SSOState
from app.page_access import get_access_settings_map, get_page_visibility, is_public_path, match_page_for_path
from app.routers import auth, dashboard, admin, pi, market, system, planner, skyhook, colony_plan, pi_templates, hauling, killboard, intel, inventory
from app.templates_env import templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Sentry error tracking (optional) ──────────────────────────────────────────
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry initialized.")

settings = get_settings()

# Celery Beat handles scheduled jobs (market refresh, SSO cleanup, colony refresh).
# APScheduler is kept as a fallback only when CELERY_BROKER_URL is not set,
# so the app still works without RabbitMQ in dev/single-process setups.
_USE_CELERY = bool(os.getenv("CELERY_BROKER_URL"))


def _fallback_refresh_market_prices():
    """Fallback market refresh — only used when Celery is not configured."""
    from app.market import refresh_all_pi_prices
    from app.routers.dashboard import refresh_dashboard_price_cache
    from app.routers.skyhook import refresh_skyhook_value_cache
    db = SessionLocal()
    try:
        logger.info("Starte Marktpreis-Refresh (APScheduler fallback)...")
        refresh_all_pi_prices(db)
        refresh_dashboard_price_cache(db)
        refresh_skyhook_value_cache(db)
    except Exception as e:
        logger.warning("Marktpreis-Refresh fehlgeschlagen: %s", e)
    finally:
        db.close()


def _fallback_cleanup_sso():
    """Fallback SSO cleanup — only used when Celery is not configured."""
    try:
        with SessionLocal() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            deleted = db.query(SSOState).filter(SSOState.created_at < cutoff).delete()
            db.commit()
            if deleted:
                logger.info("Bereinigt: %d abgelaufene SSO-States", deleted)
    except Exception as e:
        logger.warning("SSO-State-Bereinigung fehlgeschlagen: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — fail fast on missing/default config
    if settings.secret_key == "change-me-to-a-secure-random-key-32chars":
        raise RuntimeError("SECRET_KEY ist nicht konfiguriert. Bitte SECRET_KEY in .env setzen.")
    if not settings.eve_client_id:
        raise RuntimeError("EVE_CLIENT_ID ist nicht konfiguriert.")
    if not settings.eve_client_secret:
        raise RuntimeError("EVE_CLIENT_SECRET ist nicht konfiguriert.")

    logger.info("PlanetFlow startet...")
    from app import sde
    sde.init()
    inserted_translations = bootstrap_translations()
    if inserted_translations:
        logger.info("I18N: %s Uebersetzungen in DB gebootstrapped.", inserted_translations)
    inserted_type_translations = bootstrap_pi_type_translations()
    if inserted_type_translations:
        logger.info("I18N: %s PI-Type-Uebersetzungen aus SDE in DB gebootstrapped.", inserted_type_translations)
    inserted_static_planets = bootstrap_static_planets()
    if inserted_static_planets:
        logger.info("SDE: %s statische Planeten in DB gebootstrapped.", inserted_static_planets)
    inserted_static_stargates = bootstrap_static_stargates()
    if inserted_static_stargates:
        logger.info("SDE: %s statische Stargates/Gate-Distanzen in DB gebootstrapped.", inserted_static_stargates)

    _fallback_cleanup_sso()

    if not _USE_CELERY:
        # Dev mode: run scheduled jobs in-process via APScheduler
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(_fallback_refresh_market_prices, "interval", minutes=15)
        scheduler.add_job(_fallback_cleanup_sso, "interval", hours=1)
        scheduler.start()
        logger.info("APScheduler gestartet (dev-Fallback, kein Celery konfiguriert).")
    else:
        scheduler = None
        logger.info("Celery erkannt — APScheduler deaktiviert. Jobs laufen via Celery Beat.")

    yield

    # Shutdown
    if scheduler:
        scheduler.shutdown(wait=False)
    logger.info("PlanetFlow beendet.")


app = FastAPI(
    title="PlanetFlow",
    description="Planetary Industry Dashboard für EVE Online",
    version="1.0.0",
    lifespan=lifespan,
)

# Statische Dateien
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def impersonate_middleware(request: Request, call_next):
    from app.session import read_session
    from app.models import Account

    session = read_session(request)
    request.state.is_impersonating = bool(session and session.get("real_owner_id"))
    request.state.real_owner_id = session.get("real_owner_id") if session else None

    request.state.account = None
    request.state.page_permissions = {}
    request.state.page_access_levels = {}

    path = request.url.path
    if is_public_path(path):
        return await call_next(request)

    if path == "/manager/impersonate-exit" and request.state.is_impersonating:
        return await call_next(request)

    db = SessionLocal()
    try:
        account_id = session.get("account_id") if session else None
        account = db.query(Account).filter(Account.id == account_id).first() if account_id else None
        request.state.account = account
        settings_map = get_access_settings_map(db)
        request.state.page_access_levels = settings_map
        request.state.page_permissions = get_page_visibility(account, settings_map=settings_map)

        page = match_page_for_path(path)
        if page is None:
            return await call_next(request)

        if account is None:
            return RedirectResponse(url="/", status_code=303)

        if not request.state.page_permissions.get(page.key, True):
            if page.admin_only:
                level = "admin"
            else:
                level = settings_map.get(page.key, page.default_access)
            return templates.TemplateResponse("access_denied.html", {
                "request": request,
                "account": account,
                "required_role": level,
            }, status_code=403)
        return await call_next(request)
    finally:
        db.close()

# Router einbinden
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(pi.router)
app.include_router(market.router)
app.include_router(system.router)
app.include_router(planner.router)
app.include_router(inventory.router)
app.include_router(skyhook.router)
app.include_router(hauling.router)
app.include_router(killboard.router)
app.include_router(colony_plan.router)
app.include_router(pi_templates.router)
app.include_router(intel.router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    from app.session import read_session
    from app.database import get_db
    from app.models import Account

    session = read_session(request)
    error = request.query_params.get("error")
    db = SessionLocal()
    try:
        if session:
            account = db.query(Account).filter(Account.id == session.get("account_id")).first()
            if account:
                return RedirectResponse(url="/dashboard", status_code=302)
        has_owner = db.query(Account).filter(Account.is_owner == True).first() is not None
    finally:
        db.close()
    return templates.TemplateResponse("index.html", {"request": request, "error": error, "has_owner": has_owner})


@app.get("/health")
def health_check():
    status = {}

    # Database
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["database"] = "ok"
    except Exception as e:
        logger.error("health: database check failed: %s", e)
        status["database"] = "error"

    # RabbitMQ / Celery broker (optional)
    broker_url = os.getenv("CELERY_BROKER_URL", "")
    if broker_url:
        try:
            import amqp
            parts = broker_url.replace("amqp://", "").split("@")
            creds, hostpart = parts[0], parts[1].split("/")[0]
            user, pwd = creds.split(":", 1)
            host, port = (hostpart.split(":") + ["5672"])[:2]
            conn = amqp.Connection(host=f"{host}:{port}", userid=user, password=pwd)
            conn.connect()
            conn.close()
            status["rabbitmq"] = "ok"
        except Exception as e:
            logger.error("health: rabbitmq check failed: %s", e)
            status["rabbitmq"] = "error"
    else:
        status["rabbitmq"] = "not_configured"

    overall = "ok" if all(v in ("ok", "not_configured") for v in status.values()) else "degraded"
    return {"status": overall, **status}
