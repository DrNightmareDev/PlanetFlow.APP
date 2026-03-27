from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.database import engine, SessionLocal
from app.i18n import bootstrap_pi_type_translations, bootstrap_static_planets, bootstrap_translations
from app.models import SSOState
from app.routers import auth, dashboard, admin, pi, market, system, planner, skyhook, colony_plan
from app.templates_env import templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()



def cleanup_old_sso_states():
    """Löscht abgelaufene SSO States (älter als 1 Stunde)."""
    try:
        with SessionLocal() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            deleted = db.query(SSOState).filter(SSOState.created_at < cutoff).delete()
            db.commit()
            if deleted:
                logger.info(f"Bereinigt: {deleted} abgelaufene SSO-States")
    except Exception as e:
        logger.warning(f"SSO-State-Bereinigung fehlgeschlagen: {e}")


def refresh_market_prices():
    """Stündlicher Marktpreis-Refresh via Janice API."""
    from app.market import refresh_all_pi_prices
    from app.routers.dashboard import refresh_dashboard_price_cache
    from app.routers.skyhook import refresh_skyhook_value_cache
    db = SessionLocal()
    try:
        logger.info("Starte stündlichen Marktpreis-Refresh...")
        refresh_all_pi_prices(db)
        refresh_dashboard_price_cache(db)
        refresh_skyhook_value_cache(db)
        logger.info("Marktpreis- und Wert-Cache-Refresh abgeschlossen.")
    except Exception as e:
        logger.warning(f"Marktpreis-Refresh fehlgeschlagen: {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(refresh_market_prices, 'interval', minutes=15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("EVE PI Manager startet...")
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
    cleanup_old_sso_states()
    scheduler.start()
    logger.info("APScheduler gestartet (15-min Marktpreis-Refresh).")
    yield
    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("EVE PI Manager beendet.")


app = FastAPI(
    title="EVE PI Manager",
    description="Planetary Industry Dashboard für EVE Online",
    version="1.0.0",
    lifespan=lifespan,
)

# Statische Dateien
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def impersonate_middleware(request: Request, call_next):
    from app.session import read_session
    session = read_session(request)
    request.state.is_impersonating = bool(session and session.get("real_owner_id"))
    request.state.real_owner_id = session.get("real_owner_id") if session else None
    return await call_next(request)

# Router einbinden
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(pi.router)
app.include_router(market.router)
app.include_router(system.router)
app.include_router(planner.router)
app.include_router(skyhook.router)
app.include_router(colony_plan.router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    from app.session import read_session
    from app.database import get_db
    from app.models import Account

    session = read_session(request)
    if session:
        db = SessionLocal()
        try:
            account = db.query(Account).filter(
                Account.id == session.get("account_id")
            ).first()
            if account:
                return RedirectResponse(url="/dashboard", status_code=302)
        finally:
            db.close()

    error = request.query_params.get("error")
    db = SessionLocal()
    try:
        from app.models import Account
        has_owner = db.query(Account).filter(Account.is_owner == True).first() is not None
    finally:
        db.close()
    return templates.TemplateResponse("index.html", {"request": request, "error": error, "has_owner": has_owner})


@app.get("/health")
def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}
