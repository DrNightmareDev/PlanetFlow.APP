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
from app.i18n import bootstrap_pi_type_translations, bootstrap_static_planets, bootstrap_static_stargates, bootstrap_translations, reseed_translations
from app.models import Character, SSOState
from app.page_access import (
    get_access_settings_map,
    get_billing_enabled,
    get_page_visibility,
    get_subscription_badge_settings_map,
    is_public_path,
    match_page_for_path,
)
from app.security import decrypt_text, encrypt_text, require_strong_secret_key
from app.routers import auth, dashboard, admin, director, pi, market, system, planner, skyhook, colony_plan, pi_templates, hauling, killboard, intel, inventory, billing, killintel
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
# APScheduler is kept as a fallback only when RabbitMQ/Celery is not configured,
# so the app still works without RabbitMQ in dev/single-process setups.
_USE_CELERY = bool(settings.celery_broker_url)


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


def _encrypt_stored_tokens() -> None:
    """One-time in-place migration for legacy plaintext OAuth tokens."""
    db = SessionLocal()
    try:
        updated = 0
        characters = db.query(Character).filter(
            (Character.access_token.isnot(None)) | (Character.refresh_token.isnot(None))
        ).all()
        for character in characters:
            changed = False
            if character.access_token and decrypt_text(character.access_token) == character.access_token:
                character.access_token = encrypt_text(character.access_token)
                changed = True
            if character.refresh_token and decrypt_text(character.refresh_token) == character.refresh_token:
                character.refresh_token = encrypt_text(character.refresh_token)
                changed = True
            if changed:
                updated += 1
        if updated:
            db.commit()
            logger.info("Encrypted legacy OAuth tokens for %d characters.", updated)
        else:
            db.rollback()
    except Exception as exc:
        db.rollback()
        logger.warning("OAuth token re-encryption skipped: %s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — fail fast on missing/default config
    require_strong_secret_key()
    if not settings.eve_client_id:
        raise RuntimeError("EVE_CLIENT_ID ist nicht konfiguriert.")
    if not settings.eve_client_secret:
        raise RuntimeError("EVE_CLIENT_SECRET ist nicht konfiguriert.")
    if settings.eve_owner_character_id <= 0:
        raise RuntimeError("EVE_OWNER_CHARACTER_ID muss gesetzt sein, bevor die App startet.")

    logger.info("PlanetFlow startet...")
    from app import sde
    sde.init()
    _encrypt_stored_tokens()
    reseed_result = reseed_translations()
    if reseed_result["inserted"] or reseed_result["updated"]:
        logger.info("I18N: %s eingefuegt, %s aktualisiert (reseed).", reseed_result["inserted"], reseed_result["updated"])
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
    request.state.page_subscription_badges = {}
    request.state.billing_enabled = False
    request.state.show_director_nav = False

    path = request.url.path
    if is_public_path(path):
        response = await call_next(request)
        from app.session import set_csrf_cookie_if_needed
        set_csrf_cookie_if_needed(request, response)
        return response

    if path == "/admin/impersonate-exit" and request.state.is_impersonating:
        return await call_next(request)

    db = SessionLocal()
    try:
        account_id = session.get("account_id") if session else None
        account = db.query(Account).filter(Account.id == account_id).first() if account_id else None
        request.state.account = account
        billing_enabled = get_billing_enabled(db)
        request.state.billing_enabled = billing_enabled

        settings_map = get_access_settings_map(db)
        request.state.page_access_levels = settings_map
        # Subscription badges and entitlements are only active when billing is enabled
        if billing_enabled:
            badge_map = get_subscription_badge_settings_map(db)
            request.state.page_subscription_badges = badge_map
        else:
            request.state.page_subscription_badges = {}

        # Load entitlement cache once per request (only for paid pages, avoids extra query otherwise)
        entitlement_map: dict[str, bool] | None = None
        if billing_enabled and account is not None and any("paid" in str(v).split(",") for v in settings_map.values()):
            from app.services.entitlements import get_cached_page_entitlements
            entitlement_map = get_cached_page_entitlements(db, account_id=account.id)
        request.state.entitlement_map = entitlement_map or {}

        request.state.page_permissions = get_page_visibility(
            account, db=db, settings_map=settings_map, entitlement_map=entitlement_map
        )

        if account is not None:
            from app.esi import get_corporation_info
            from app.models import Character
            is_ceo = False
            main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
            if not main_char:
                main_char = db.query(Character).filter(Character.account_id == account.id).first()
            corp_id = main_char.corporation_id if main_char else None
            if corp_id:
                try:
                    ceo_id = (get_corporation_info(corp_id) or {}).get("ceo_id")
                    if ceo_id:
                        is_ceo = db.query(Character).filter(
                            Character.account_id == account.id,
                            Character.eve_character_id == ceo_id,
                        ).first() is not None
                except Exception:
                    is_ceo = False
            request.state.show_director_nav = bool(account.is_director or is_ceo)

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
        response = await call_next(request)
        from app.session import set_csrf_cookie_if_needed
        set_csrf_cookie_if_needed(request, response)
        return response
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
app.include_router(killintel.router)
app.include_router(billing.router)
app.include_router(director.router)


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
        from app.config import get_settings as _gs
        has_owner = bool(_gs().eve_owner_character_id)
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
    broker_url = settings.celery_broker_url
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
