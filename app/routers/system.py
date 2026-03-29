import re

import requests
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.dependencies import require_account, require_admin
from app.esi import ensure_valid_token, get_character_fittings, get_system_info, get_planet_info
from app.i18n import get_language_from_request, translate, translate_type_name
from app.market import get_prices_by_names, get_market_trends, PI_TYPE_IDS
from app.models import StaticPlanet
from app.pi_analyzer import analyze_system
from app.pi_data import PLANET_TYPE_COLORS, PLANET_RESOURCES, P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4
from app import sde
from app.sde import search_systems_local, search_constellations_local, get_constellation_systems_local
from app.templates_env import templates

router = APIRouter(prefix="/system", tags=["system"])

_system_planet_cache: dict[int, list[dict]] = {}

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


ROMAN_NUMERALS = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
}


def _resolve_type_id(name: str) -> int | None:
    return PI_TYPE_IDS.get(name) or sde.find_type_id_by_name(name)


def _all_pi_names() -> set[str]:
    names = set(P0_TO_P1.keys()) | set(P0_TO_P1.values()) | set(P1_TO_P2.keys()) | set(P2_TO_P3.keys()) | set(P3_TO_P4.keys())
    names |= {item for values in P1_TO_P2.values() for item in values}
    names |= {item for values in P2_TO_P3.values() for item in values}
    names |= {item for values in P3_TO_P4.values() for item in values}
    return names


def _build_product_labels(lang: str) -> dict[str, str]:
    return {
        name: translate_type_name(_resolve_type_id(name), fallback=name, lang=lang)
        for name in _all_pi_names()
    }


def _build_planet_type_labels(lang: str) -> dict[str, str]:
    return {
        name: translate(f"planet_type.{name.lower()}", lang)
        for name in PLANET_TYPE_COLORS.keys()
    }


FITTINGS_SCOPE = "esi-fittings.read_fittings.v1"
FITTING_SLOT_GROUPS = (
    "high",
    "mid",
    "low",
    "rig",
    "subsystem",
    "service",
    "drone",
    "cargo",
    "fighter",
    "implant",
    "booster",
    "other",
)
FITTING_SLOT_LABELS = {
    "high": "High Slots",
    "mid": "Mid Slots",
    "low": "Low Slots",
    "rig": "Rig Slots",
    "subsystem": "Subsystems",
    "service": "Service Slots",
    "drone": "Drone Bay",
    "cargo": "Cargo",
    "fighter": "Fighter Bay",
    "implant": "Implants",
    "booster": "Boosters",
    "other": "Other",
}
_FLAG_INDEX_RE = re.compile(r"(\d+)$")


def _scope_set(raw_scopes: str | None) -> set[str]:
    if not raw_scopes:
        return set()
    return {part.strip() for part in raw_scopes.replace(",", " ").split() if part.strip()}


def _flag_sort_index(flag: str | None) -> int:
    if not flag:
        return 999
    match = _FLAG_INDEX_RE.search(flag)
    return int(match.group(1)) if match else 999


def _flag_to_slot_group(flag: str | None) -> str:
    normalized = (flag or "").lower()
    if normalized.startswith("hislot"):
        return "high"
    if normalized.startswith("medslot"):
        return "mid"
    if normalized.startswith("loslot"):
        return "low"
    if normalized.startswith("rigslot"):
        return "rig"
    if normalized.startswith("subsystemslot"):
        return "subsystem"
    if normalized.startswith("serviceslot"):
        return "service"
    if "drone" in normalized:
        return "drone"
    if "cargo" in normalized:
        return "cargo"
    if "fighter" in normalized:
        return "fighter"
    if "implant" in normalized:
        return "implant"
    if "booster" in normalized:
        return "booster"
    return "other"


def _build_fitting_item(raw_item: dict) -> dict:
    flag = raw_item.get("flag") or ""
    type_id = int(raw_item.get("type_id") or 0)
    quantity = int(raw_item.get("quantity") or 1)
    name = sde.get_type_name(type_id) or f"Type {type_id}"
    slot_group = _flag_to_slot_group(flag)
    return {
        "type_id": type_id,
        "name": name,
        "flag": flag,
        "quantity": quantity,
        "slot_group": slot_group,
        "slot_label": FITTING_SLOT_LABELS.get(slot_group, "Other"),
        "sort_index": _flag_sort_index(flag),
        "icon_url": f"https://images.evetech.net/types/{type_id}/icon?size=64" if type_id else None,
    }


def _normalize_fitting(raw_fitting: dict) -> dict:
    ship_type_id = int(raw_fitting.get("ship_type_id") or 0)
    items = [_build_fitting_item(item) for item in (raw_fitting.get("items") or [])]
    items.sort(key=lambda item: (FITTING_SLOT_GROUPS.index(item["slot_group"]) if item["slot_group"] in FITTING_SLOT_GROUPS else 999, item["sort_index"], item["name"]))
    return {
        "fitting_id": int(raw_fitting.get("fitting_id") or 0),
        "name": raw_fitting.get("name") or "Unnamed Fit",
        "description": raw_fitting.get("description") or "",
        "ship_type_id": ship_type_id,
        "ship_name": sde.get_type_name(ship_type_id) or f"Type {ship_type_id}",
        "ship_icon_url": f"https://images.evetech.net/types/{ship_type_id}/render?size=256" if ship_type_id else None,
        "items": items,
    }


def _serialize_character_fittings(character, db: Session) -> dict:
    data = {
        "character_id": int(character.eve_character_id),
        "character_name": character.character_name,
        "portrait_url": character.portrait_url,
        "scopes": sorted(_scope_set(character.scopes)),
        "status": "ok",
        "warning": None,
        "refresh_url": "/auth/refresh-scopes",
        "fittings": [],
    }
    scopes = _scope_set(character.scopes)
    if FITTINGS_SCOPE not in scopes:
        data["status"] = "missing_scope"
        data["warning"] = "Fittings konnten nicht gelesen werden. Der Scope 'esi-fittings.read_fittings.v1' fehlt."
        return data

    access_token = ensure_valid_token(character, db)
    if not access_token:
        data["status"] = "auth_error"
        data["warning"] = "Fittings konnten nicht gelesen werden. Fuer diesen Charakter ist kein gueltiger Token vorhanden."
        return data

    try:
        raw_fittings = get_character_fittings(int(character.eve_character_id), access_token)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        data["status"] = "esi_error"
        if status_code in (401, 403):
            data["warning"] = "ESI verweigert den Zugriff auf die Fittings. Bitte die Scopes aktualisieren und den Charakter erneut autorisieren."
        else:
            data["warning"] = f"ESI-Fehler beim Laden der Fittings ({status_code or 'unbekannt'})."
        return data
    except Exception as exc:
        data["status"] = "esi_error"
        data["warning"] = f"Fittings konnten nicht geladen werden: {exc}"
        return data

    data["fittings"] = [_normalize_fitting(entry) for entry in raw_fittings]
    return data


def _extract_planet_number(planet_name: str, system_name: str | None = None) -> str | None:
    if not planet_name:
        return None
    cleaned = planet_name.strip()
    if system_name and cleaned.lower().startswith(system_name.lower()):
        cleaned = cleaned[len(system_name):].strip()
    token = cleaned.split()[-1] if cleaned.split() else ""
    return token if token in ROMAN_NUMERALS else None


def _get_static_planet_rows(system_id: int) -> dict[int, StaticPlanet]:
    with SessionLocal() as db:
        rows = db.query(StaticPlanet).filter(StaticPlanet.system_id == system_id).all()
        return {int(row.planet_id): row for row in rows}


def _load_system_planets(system_id: int) -> list[dict]:
    cached = _system_planet_cache.get(system_id)
    if cached is not None:
        return cached

    local_info = sde.get_system_local(system_id)
    system_name = local_info["name"] if local_info else None
    region_name = local_info["region_name"] if local_info else None

    system_info = get_system_info(system_id)
    if not system_info:
        return []

    planet_ids = system_info.get("planets", [])
    planet_ids = [p.get("planet_id") if isinstance(p, dict) else p for p in planet_ids]
    static_rows = _get_static_planet_rows(system_id)

    planets = []
    for pid in planet_ids[:16]:
        pinfo = get_planet_info(pid)
        static_row = static_rows.get(int(pid))
        type_id = pinfo.get("type_id")
        raw_type = ""
        if type_id:
            type_name = sde.get_type_name(type_id) or ""
            if "(" in type_name and type_name.endswith(")"):
                raw_type = type_name[type_name.index("(") + 1:-1].lower()
        mapped = PLANET_TYPE_MAP.get(raw_type)
        if mapped:
            planets.append({
                "planet_id": pid,
                "planet_name": (static_row.planet_name if static_row else None) or pinfo.get("name", f"Planet {pid}"),
                "planet_type": mapped,
                "planet_number": (static_row.planet_number if static_row else None) or _extract_planet_number(
                    (static_row.planet_name if static_row else None) or pinfo.get("name", f"Planet {pid}"),
                    system_name or system_info.get("name"),
                ),
                "radius": static_row.radius if static_row else pinfo.get("radius"),
                "system_id": system_id,
                "system_name": system_name or system_info.get("name") or f"System {system_id}",
                "region_name": region_name,
                "constellation_name": local_info.get("constellation_name") if local_info else None,
            })
    _system_planet_cache[system_id] = planets
    return planets


def _build_product_locations(planets_needed: list[str], selected_planets: list[dict]) -> list[dict]:
    planet_type_set = set(planets_needed or [])
    grouped: dict[tuple[int, str], dict] = {}
    for planet in selected_planets:
        if planet.get("planet_type") not in planet_type_set:
            continue
        key = (planet["system_id"], planet["system_name"])
        if key not in grouped:
            grouped[key] = {
                "system_id": planet["system_id"],
                "system_name": planet["system_name"],
                "region_name": planet.get("region_name"),
                "constellation_name": planet.get("constellation_name"),
                "planets": [],
            }
        grouped[key]["planets"].append({
            "planet_id": planet["planet_id"],
            "planet_name": planet["planet_name"],
            "planet_type": planet["planet_type"],
        })

    results = []
    for system in grouped.values():
        system["planets"].sort(key=lambda item: (item["planet_type"], item["planet_name"]))
        results.append(system)
    results.sort(key=lambda item: item["system_name"])
    return results


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def system_analyzer(
    request: Request,
    account=Depends(require_account),
):
    lang = get_language_from_request(request)
    return templates.TemplateResponse("system.html", {
        "request": request,
        "account": account,
        "planet_type_colors": PLANET_TYPE_COLORS,
        "planet_resources": PLANET_RESOURCES,
        "p0_to_p1": P0_TO_P1,
        "product_labels": _build_product_labels(lang),
        "planet_type_labels": _build_planet_type_labels(lang),
    })


@router.get("/search")
def search_system(q: str, account=Depends(require_account)):
    if len(q) < 3:
        return JSONResponse(content={"systems": []})
    try:
        systems = search_systems_local(q, limit=10)
        return JSONResponse(content={"systems": systems})
    except Exception as e:
        return JSONResponse(content={"systems": [], "error": str(e)})


@router.get("/search/constellations")
def search_constellations(q: str, account=Depends(require_account)):
    if len(q) < 3:
        return JSONResponse(content={"constellations": []})
    try:
        constellations = search_constellations_local(q, limit=10)
        return JSONResponse(content={"constellations": constellations})
    except Exception as e:
        return JSONResponse(content={"constellations": [], "error": str(e)})


@router.get("/constellations/{constellation_id}/systems")
def constellation_systems(constellation_id: int, account=Depends(require_account)):
    try:
        systems = get_constellation_systems_local(constellation_id)
        return JSONResponse(content={"systems": systems})
    except Exception as e:
        return JSONResponse(content={"systems": [], "error": str(e)})


@router.get("/analyze/{system_id}")
def analyze(system_id: int, account=Depends(require_account)):
    try:
        # Lokale System-Infos (kein ESI-Call nötig)
        local_info = sde.get_system_local(system_id)

        if local_info:
            system_name = local_info["name"]
            system_security = local_info["security"]
            region_name = local_info["region_name"]
        else:
            # Fallback: ESI
            system_info = get_system_info(system_id)
            if not system_info:
                return JSONResponse(content={"error": "System nicht gefunden"}, status_code=404)
            system_name = system_info.get("name")
            system_security = system_info.get("security_status", 0.0)
            region_name = None

        # Planetenliste via ESI (1 Call)
        system_info = get_system_info(system_id)
        if not system_info:
            return JSONResponse(content={"error": "System nicht gefunden"}, status_code=404)
        planet_ids = system_info.get("planets", [])
        planet_ids = [p.get("planet_id") if isinstance(p, dict) else p for p in planet_ids]
        static_rows = _get_static_planet_rows(system_id)

        planet_types = []
        planet_details = []
        type_count: dict = {}
        type_resources: dict = {}
        for pid in planet_ids[:16]:  # Max 16 Planeten
            pinfo = get_planet_info(pid)
            static_row = static_rows.get(int(pid))
            # ESI returns type_id (int) → resolve via SDE types
            type_id = pinfo.get("type_id")
            raw_type = ""
            if type_id:
                type_name = sde.get_type_name(type_id) or ""
                if "(" in type_name and type_name.endswith(")"):
                    raw_type = type_name[type_name.index("(") + 1:-1].lower()
            mapped = PLANET_TYPE_MAP.get(raw_type)
            if mapped:
                planet_types.append(mapped)
                planet_name = (static_row.planet_name if static_row else None) or pinfo.get("name", f"Planet {pid}")
                planet_details.append({
                    "id": pid,
                    "name": planet_name,
                    "type": mapped,
                    "color": PLANET_TYPE_COLORS.get(mapped, "#586e75"),
                    "number": (static_row.planet_number if static_row else None) or _extract_planet_number(planet_name, system_name),
                    "radius": static_row.radius if static_row else pinfo.get("radius"),
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
        rec_names = [r["name"] for r in recommendations]

        # Preise: Sell + Buy + Angebot (eine Fuzzwork Batch-Anfrage)
        prices = get_prices_by_names(rec_names)

        # Preistrends: ESI Market History, 24h gecacht, parallel gefetcht
        unique_type_ids = list({PI_TYPE_IDS[n] for n in rec_names if n in PI_TYPE_IDS})
        trends = get_market_trends(unique_type_ids)

        for rec in recommendations:
            price_data = prices.get(rec["name"], {})
            rec["isk_sell"] = price_data.get("sell", 0.0)
            rec["isk_buy"] = price_data.get("buy", 0.0)
            # Angebot-Indikator anhand Anzahl Sell-Orders in Jita
            order_count = int(price_data.get("sell_order_count", 0))
            if order_count == 0:
                rec["supply_level"] = "none"
            elif order_count <= 30:
                rec["supply_level"] = "green"
            elif order_count <= 150:
                rec["supply_level"] = "yellow"
            else:
                rec["supply_level"] = "red"
            # Trend-Daten aus ESI History
            type_id = PI_TYPE_IDS.get(rec["name"])
            trend_data = trends.get(type_id, {}) if type_id else {}
            rec["trend_1d"] = trend_data.get("trend_1d")
            rec["trend_7d"] = trend_data.get("trend_7d")
            rec["trend_30d"] = trend_data.get("trend_30d")

        # Nach Jita Sell absteigend sortieren
        recommendations.sort(key=lambda x: x.get("isk_sell", 0), reverse=True)

        return JSONResponse(content={
            "system_name": system_name,
            "security": round(system_security, 2),
            "true_sec": round(system_security, 4),
            "region": region_name,
            "planets": planet_details,
            "planet_types": planet_types_summary,
            "recommendations": recommendations,
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/compare", response_class=HTMLResponse)
def compare_page(
    request: Request,
    account=Depends(require_account),
):
    lang = get_language_from_request(request)
    return templates.TemplateResponse("compare.html", {
        "request": request,
        "account": account,
        "planet_type_colors": PLANET_TYPE_COLORS,
        "product_labels": _build_product_labels(lang),
        "planet_type_labels": _build_planet_type_labels(lang),
    })


@router.get("/mix", response_class=HTMLResponse)
def system_mix_page(
    request: Request,
    account=Depends(require_account),
):
    return templates.TemplateResponse("system_mix.html", {
        "request": request,
        "account": account,
        "planet_type_colors": PLANET_TYPE_COLORS,
    })


@router.get("/mix/analyze")
def system_mix_analyze(
    system_ids: str = "",
    constellation_ids: str = "",
    account=Depends(require_account),
):
    ids = []
    for raw in (system_ids or "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            ids.append(int(raw))
    constellation_id_values = []
    for raw in (constellation_ids or "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            constellation_id_values.append(int(raw))

    selected_system_map = {}
    for system_id in ids:
        selected_system_map[system_id] = {"source": "system"}
    selected_constellations = []
    for constellation_id in list(dict.fromkeys(constellation_id_values)):
        systems = get_constellation_systems_local(constellation_id)
        if not systems:
            continue
        selected_constellations.append({
            "id": constellation_id,
            "name": systems[0].get("constellation"),
            "region": systems[0].get("region"),
            "system_count": len(systems),
        })
        for system in systems:
            selected_system_map.setdefault(system["id"], {"source": "constellation"})

    ids = list(selected_system_map.keys())
    if not ids:
        return JSONResponse({
            "systems": [],
            "constellations": selected_constellations,
            "planet_types": [],
            "products": {"P4": [], "P3": [], "P2": []},
        })

    selected_systems = []
    selected_planets = []
    combined_planet_types = []
    type_counts: dict[str, int] = {}

    for system_id in ids:
        planets = _load_system_planets(system_id)
        if not planets:
            continue
        sys_name = planets[0]["system_name"]
        region_name = planets[0]["region_name"]
        selected_systems.append({
            "id": system_id,
            "name": sys_name,
            "region": region_name,
            "constellation": planets[0].get("constellation_name"),
            "planet_count": len(planets),
            "source": selected_system_map.get(system_id, {}).get("source", "system"),
        })
        for planet in planets:
            selected_planets.append(planet)
            combined_planet_types.append(planet["planet_type"])
            type_counts[planet["planet_type"]] = type_counts.get(planet["planet_type"], 0) + 1

    recommendations = analyze_system(combined_planet_types)
    rec_names = [r["name"] for r in recommendations]
    prices = get_prices_by_names(rec_names)
    grouped = {"P4": [], "P3": [], "P2": []}
    for rec in recommendations:
        tier = rec.get("tier")
        if tier not in grouped:
            continue
        price_data = prices.get(rec["name"], {})
        grouped[tier].append({
            "name": rec["name"],
            "tier": tier,
            "inputs": rec.get("inputs", []),
            "planets_needed": rec.get("planets_needed", []),
            "score": rec.get("score", 0),
            "jita_sell": price_data.get("sell", 0.0),
            "jita_buy": price_data.get("buy", 0.0),
            "locations": _build_product_locations(rec.get("planets_needed", []), selected_planets),
        })

    planet_types = [
        {
            "type": pt,
            "count": count,
            "color": PLANET_TYPE_COLORS.get(pt, "#586e75"),
        }
        for pt, count in sorted(type_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    return JSONResponse({
        "systems": selected_systems,
        "constellations": selected_constellations,
        "planet_types": planet_types,
        "planet_count": len(selected_planets),
        "products": grouped,
    })


@router.get("/fittings", response_class=HTMLResponse)
def fittings_compare_page(
    request: Request,
    account=Depends(require_admin),
):
    return templates.TemplateResponse("fittings_compare.html", {
        "request": request,
        "account": account,
        "required_scope": FITTINGS_SCOPE,
    })


@router.get("/fittings/data")
def fittings_compare_data(
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    characters = sorted(
        account.characters,
        key=lambda char: (
            0 if account.main_character_id and char.id == account.main_character_id else 1,
            char.character_name.lower(),
        ),
    )
    return JSONResponse({
        "required_scope": FITTINGS_SCOPE,
        "slot_labels": FITTING_SLOT_LABELS,
        "characters": [_serialize_character_fittings(character, db) for character in characters],
    })


@router.get("/{system_query}", response_class=HTMLResponse)
def system_analyzer_direct(
    request: Request,
    system_query: str,
    account=Depends(require_account),
):
    """Direktlink: /system/Jita oder /system/30000142"""
    lang = get_language_from_request(request)
    preset_system = sde.find_system(system_query)
    preset_system_json = None
    if preset_system:
        preset_system_json = {
            "id": int(preset_system.get("id", 0) or 0),
            "name": str(preset_system.get("name", "")),
            "security": float(preset_system.get("security", 0.0) or 0.0),
            "region": str(preset_system.get("region", "") or ""),
            "constellation": str(preset_system.get("constellation", "") or ""),
        }
    preset_error = None if preset_system else f'System "{system_query}" nicht gefunden.'
    return templates.TemplateResponse("system.html", {
        "request": request,
        "account": account,
        "planet_type_colors": PLANET_TYPE_COLORS,
        "planet_resources": PLANET_RESOURCES,
        "p0_to_p1": P0_TO_P1,
        "product_labels": _build_product_labels(lang),
        "planet_type_labels": _build_planet_type_labels(lang),
        "preset_system": preset_system,
        "preset_system_json": preset_system_json,
        "preset_error": preset_error,
    })
