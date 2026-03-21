from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.esi import search_systems_auth, get_system_info, get_planet_info, ensure_valid_token
from app.market import get_sell_prices_by_names
from app.models import Character
from app.pi_analyzer import analyze_system
from app.pi_data import PLANET_TYPE_COLORS, PLANET_RESOURCES
from app.templates_env import templates

router = APIRouter(prefix="/system", tags=["system"])

PLANET_TYPE_MAP = {
    "temperate": "Temperate",
    "barren": "Barren",
    "oceanic": "Oceanic",
    "ice": "Ice",
    "gas": "Gas",
    "lava": "Lava",
    "storm": "Storm",
    "plasma": "Plasma",
}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def system_analyzer(
    request: Request,
    account=Depends(require_account),
):
    return templates.TemplateResponse("system.html", {
        "request": request,
        "account": account,
        "planet_type_colors": PLANET_TYPE_COLORS,
    })


@router.get("/search")
def search_system(q: str, account=Depends(require_account), db: Session = Depends(get_db)):
    if len(q) < 3:
        return JSONResponse(content={"systems": []})
    try:
        # Hauptcharakter für Auth-Suche ermitteln
        char = None
        if account.main_character_id:
            char = db.query(Character).filter(Character.id == account.main_character_id).first()
        if not char:
            char = db.query(Character).filter(Character.account_id == account.id).first()
        if not char:
            return JSONResponse(content={"systems": []})

        access_token = ensure_valid_token(char, db)
        if not access_token:
            return JSONResponse(content={"systems": []})

        result = search_systems_auth(char.eve_character_id, access_token, q)
        system_ids = result.get("solar_system", [])[:10]
        systems = []
        for sid in system_ids:
            info = get_system_info(sid)
            if info:
                systems.append({
                    "id": sid,
                    "name": info.get("name", f"System {sid}"),
                    "security": round(info.get("security_status", 0.0), 1),
                })
        return JSONResponse(content={"systems": systems})
    except Exception as e:
        return JSONResponse(content={"systems": [], "error": str(e)})


@router.get("/analyze/{system_id}")
def analyze(system_id: int, account=Depends(require_account)):
    try:
        system_info = get_system_info(system_id)
        if not system_info:
            return JSONResponse(content={"error": "System nicht gefunden"}, status_code=404)

        planet_ids = system_info.get("planets", [])
        planet_ids = [p.get("planet_id") if isinstance(p, dict) else p for p in planet_ids]

        planet_types = []
        planet_details = []
        type_count: dict = {}
        type_resources: dict = {}
        for pid in planet_ids[:16]:  # Max 16 Planeten
            pinfo = get_planet_info(pid)
            raw_type = pinfo.get("type_name", "").lower()
            mapped = PLANET_TYPE_MAP.get(raw_type)
            if mapped:
                planet_types.append(mapped)
                planet_details.append({
                    "id": pid,
                    "name": pinfo.get("name", f"Planet {pid}"),
                    "type": mapped,
                    "color": PLANET_TYPE_COLORS.get(mapped, "#586e75"),
                })
                type_count[mapped] = type_count.get(mapped, 0) + 1
                if mapped not in type_resources:
                    type_resources[mapped] = PLANET_RESOURCES.get(mapped, [])

        planet_types_summary = sorted(
            [
                {
                    "type": pt,
                    "count": type_count[pt],
                    "color": PLANET_TYPE_COLORS.get(pt, "#586e75"),
                    "resources": type_resources[pt],
                }
                for pt in type_resources
            ],
            key=lambda x: x["count"],
            reverse=True,
        )

        recommendations = analyze_system(planet_types)

        # Preise für Empfehlungen hinzufügen
        prices = get_sell_prices_by_names([r["name"] for r in recommendations])
        for rec in recommendations:
            rec["isk_sell"] = prices.get(rec["name"], 0.0)

        return JSONResponse(content={
            "system_name": system_info.get("name"),
            "security": round(system_info.get("security_status", 0.0), 2),
            "planets": planet_details,
            "planet_types": planet_types_summary,
            "recommendations": recommendations[:40],
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
