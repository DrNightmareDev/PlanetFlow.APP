from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.dependencies import require_account
from app.market import PI_TYPE_IDS, PI_TYPE_NAMES, get_jita_prices
from app.models import MarketCache
from app.templates_env import templates

router = APIRouter(prefix="/market", tags=["market"])


def _load_cached_prices(db: Session) -> dict:
    """Lädt alle gecachten Preise aus der DB ohne API-Aufruf."""
    result = {}
    caches = db.query(MarketCache).all()
    for cache in caches:
        result[cache.type_id] = {
            "best_buy": float(cache.best_buy or 0),
            "best_sell": float(cache.best_sell or 0),
            "avg_volume": float(cache.avg_volume or 0),
            "type_name": cache.type_name or PI_TYPE_NAMES.get(cache.type_id),
            "cached": True,
        }
    return result


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def market_overview(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    # Erst gecachte Preise laden und sofort rendern
    prices = {}
    try:
        prices = _load_cached_prices(db)
    except Exception:
        pass

    # Falls keine oder veraltete Daten vorhanden: fresh fetch
    if not prices:
        type_ids = list(set(PI_TYPE_IDS.values()))
        try:
            prices = get_jita_prices(type_ids, db)
        except Exception:
            pass

    # Namen zu IDs hinzufügen
    id_to_name = {v: k for k, v in PI_TYPE_IDS.items()}
    market_data = []
    seen_type_ids = set()

    for type_id, price_info in prices.items():
        if type_id in seen_type_ids:
            continue
        seen_type_ids.add(type_id)
        # Nur bekannte PI-Items anzeigen
        name = id_to_name.get(type_id) or price_info.get("type_name") or f"Type {type_id}"
        if type_id not in id_to_name.values():
            continue
        market_data.append({
            "type_id": type_id,
            "name": name,
            "best_buy": price_info.get("best_buy", 0),
            "best_sell": price_info.get("best_sell", 0),
            "avg_volume": price_info.get("avg_volume", 0),
        })

    market_data.sort(key=lambda x: x["best_sell"], reverse=True)

    return templates.TemplateResponse("market.html", {
        "request": request,
        "account": account,
        "market_data": market_data,
        "pi_type_ids": PI_TYPE_IDS,
    })
