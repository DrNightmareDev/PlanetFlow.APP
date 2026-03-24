from datetime import timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account, require_admin
from app.i18n import get_language_from_request, translate_type_name
from app.market import (
    PI_TYPE_IDS, PI_TYPE_NAMES, PI_TIERS,
    get_jita_prices, get_market_last_updated,
    can_force_market_refresh, record_force_refresh,
    refresh_all_pi_prices,
)
from app.models import MarketCache
from app.templates_env import templates

router = APIRouter(prefix="/market", tags=["market"])

TIER_COLORS = {"P1": "#586e75", "P2": "#00b4d8", "P3": "#f4a300", "P4": "#e63946"}


def _build_market_rows(db: Session, lang: str) -> list[dict]:
    """Baut die Marktdatenliste aus dem DB-Cache auf."""
    id_to_name = PI_TYPE_NAMES
    caches = {c.type_id: c for c in db.query(MarketCache).all()}
    rows = []
    seen = set()
    for name, type_id in PI_TYPE_IDS.items():
        if type_id in seen:
            continue
        seen.add(type_id)
        cache = caches.get(type_id)
        buy = float(cache.best_buy or 0) if cache else 0.0
        sell = float(cache.best_sell or 0) if cache else 0.0
        spread = round((sell - buy) / buy * 100, 1) if buy > 0 else None
        rows.append({
            "type_id": type_id,
            "name": name,
            "display_name": translate_type_name(type_id, fallback=name, lang=lang),
            "tier": PI_TIERS.get(name, "P1"),
            "buy": buy,
            "sell": sell,
            "spread": spread,
            "avg_volume": float(cache.avg_volume or 0) if cache else 0.0,
            "avg_volume_7d": float(cache.avg_volume_7d or 0) if cache else 0.0,
        })
    return rows


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def market_overview(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    lang = get_language_from_request(request)
    rows = _build_market_rows(db, lang)
    last_updated = get_market_last_updated(db)

    can_refresh, cooldown_remaining = can_force_market_refresh()

    last_updated_str = None
    last_updated_iso = None
    if last_updated:
        lu = last_updated.replace(tzinfo=timezone.utc) if last_updated.tzinfo is None else last_updated
        last_updated_str = lu.strftime("%d.%m.%Y %H:%M UTC")
        last_updated_iso = lu.astimezone(timezone.utc).isoformat()

    return templates.TemplateResponse("market.html", {
        "request": request,
        "account": account,
        "rows": rows,
        "tier_colors": TIER_COLORS,
        "last_updated": last_updated_str,
        "last_updated_iso": last_updated_iso,
        "can_refresh": can_refresh,
        "cooldown_remaining": cooldown_remaining,
    })


@router.get("/trends")
def market_trends(account=Depends(require_account)):
    """Gibt Preistrends (24h/7T/30T) für alle PI-Items zurück (async geladen)."""
    from app.market import get_market_trends
    type_ids = list(set(PI_TYPE_IDS.values()))
    trends = get_market_trends(type_ids)
    result = {}
    for name, type_id in PI_TYPE_IDS.items():
        t = trends.get(type_id, {})
        result[name] = {
            "trend_1d": t.get("trend_1d"),
            "trend_7d": t.get("trend_7d"),
            "avg_volume": t.get("avg_volume", 0.0),
            "avg_volume_7d": t.get("avg_volume_7d", 0.0),
        }
    return JSONResponse(content=result)


@router.get("/status")
def market_status(
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    last_updated = get_market_last_updated(db)
    last_updated_iso = None
    if last_updated:
        ts = last_updated.replace(tzinfo=timezone.utc) if last_updated.tzinfo is None else last_updated
        last_updated_iso = ts.astimezone(timezone.utc).isoformat()
    return JSONResponse({"last_updated_iso": last_updated_iso})


@router.post("/refresh")
def market_refresh(
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: erzwingt einen sofortigen Marktdaten-Refresh (server-weite 5-Min-Sperre)."""
    can_refresh, cooldown_remaining = can_force_market_refresh()
    if not can_refresh:
        return JSONResponse(
            content={"ok": False, "cooldown_remaining": cooldown_remaining},
            status_code=429,
        )
    record_force_refresh()
    try:
        refresh_all_pi_prices(db)
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse(content={"ok": True, "cooldown_remaining": 300})
