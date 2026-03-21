import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_planets, get_planet_detail, get_planet_info, get_schematic
from app.market import get_sell_prices_by_names
from app.models import Character
from app.pi_data import PLANET_TYPE_COLORS
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

PLANET_TYPE_NAMES = {
    "temperate": "Temperate",
    "barren": "Barren",
    "oceanic": "Oceanic",
    "ice": "Ice",
    "gas": "Gas",
    "lava": "Lava",
    "storm": "Storm",
    "plasma": "Plasma",
}


def _parse_expiry(expiry_str: str) -> datetime | None:
    """Parst einen ISO-Datetime-String, handhabt 'Z'-Suffix."""
    if not expiry_str:
        return None
    try:
        s = expiry_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _hours_until(dt: datetime | None) -> float | None:
    """Gibt Stunden bis zu einem Zeitpunkt zurück (negativ = abgelaufen)."""
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - now
    return delta.total_seconds() / 3600.0


# Fallback-Output-Mengen falls SDE nicht verfügbar
_CYCLE_QTY_FALLBACK: dict[int, int] = {1800: 20, 3600: 5, 9000: 3}


def _compute_colony_stats(pins: list, db) -> tuple[float, str | None]:
    """
    Berechnet ISK/Tag und höchsten PI-Tier.
    Nutzt SDE-Daten (exakte Mengen + Type-IDs), ESI als Fallback.
    Returns: (isk_day, highest_tier)
    """
    productions: dict[str, float] = {}  # product_name -> units_per_day
    highest_tier_num = 0

    for pin in pins:
        # Schematic-ID: erst factory_details, dann top-level
        factory = pin.get("factory_details") or {}
        schematic_id = factory.get("schematic_id") or pin.get("schematic_id")
        if not schematic_id:
            continue

        try:
            schematic = get_schematic(int(schematic_id))
        except Exception:
            continue

        cycle_time = schematic.get("cycle_time", 0)
        product_name = schematic.get("schematic_name", "")
        if not cycle_time or not product_name:
            continue

        # Tier bestimmen
        if cycle_time <= 1800:
            tier = 1
        elif cycle_time <= 3600:
            tier = 2
        elif cycle_time <= 9000:
            tier = 3
        else:
            tier = 4
        highest_tier_num = max(highest_tier_num, tier)

        # Exakte Menge aus SDE, Fallback auf bekannte Standardwerte
        qty_per_cycle = schematic.get("output_quantity") or _CYCLE_QTY_FALLBACK.get(cycle_time, 1)
        cycles_per_day = 86400.0 / float(cycle_time)
        productions[product_name] = productions.get(product_name, 0.0) + qty_per_cycle * cycles_per_day

    highest_tier = f"P{highest_tier_num}" if highest_tier_num > 0 else None

    if not productions:
        return 0.0, highest_tier

    prices = get_sell_prices_by_names(list(productions.keys()))
    isk_day = sum(qty * prices.get(name, 0.0) for name, qty in productions.items())
    logger.info(f"ISK/Tag: {isk_day:,.0f} ISK aus {productions}")
    return isk_day, highest_tier


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    db.refresh(account)

    main_char = None
    colonies = []
    planet_type_colors = PLANET_TYPE_COLORS

    if account.main_character_id:
        main_char = db.query(Character).filter(
            Character.id == account.main_character_id
        ).first()

    characters = db.query(Character).filter(
        Character.account_id == account.id
    ).all()

    total_isk_day = 0.0
    next_expiry: datetime | None = None
    next_expiry_char: str | None = None

    # Kolonien von ALLEN Charakteren des Accounts abrufen
    for char in characters:
        access_token = ensure_valid_token(char, db)
        raw_colonies = get_character_planets(char.eve_character_id, access_token or "")

        for colony in raw_colonies:
            planet_id = colony.get("planet_id")
            planet_type = colony.get("planet_type", "unknown").capitalize()

            # Planetenname von ESI holen (gecacht)
            planet_name = f"Planet {planet_id}"
            if planet_id:
                info = get_planet_info(planet_id)
                if info.get("name"):
                    planet_name = info["name"]

            # Planet-Detail holen (gecacht)
            expiry_time: datetime | None = None
            isk_day = 0.0
            highest_tier = None

            try:
                if access_token and planet_id:
                    detail = get_planet_detail(char.eve_character_id, planet_id, access_token)
                    pins = detail.get("pins", [])

                    # Früheste Extractor-Expiry ermitteln
                    for pin in pins:
                        ext = pin.get("extractor_details")
                        if ext is None:
                            continue
                        exp_str = pin.get("expiry_time")
                        if not exp_str:
                            continue
                        exp_dt = _parse_expiry(exp_str)
                        if exp_dt is not None:
                            if expiry_time is None or exp_dt < expiry_time:
                                expiry_time = exp_dt

                    # ISK/Tag und höchsten Tier berechnen
                    isk_day, highest_tier = _compute_colony_stats(pins, None)
            except Exception as e:
                logger.warning(f"Fehler bei Planet {planet_id}: {e}")

            expiry_hours = _hours_until(expiry_time)
            total_isk_day += isk_day

            # Nächste globale Expiry
            if expiry_time is not None:
                if next_expiry is None or expiry_time < next_expiry:
                    next_expiry = expiry_time
                    next_expiry_char = char.character_name

            colonies.append({
                "planet_id": planet_id,
                "planet_name": planet_name,
                "planet_type": planet_type,
                "upgrade_level": colony.get("upgrade_level", 0),
                "num_pins": colony.get("num_pins", 0),
                "last_update": colony.get("last_update", "—"),
                "solar_system_id": colony.get("solar_system_id"),
                "color": PLANET_TYPE_COLORS.get(planet_type, "#586e75"),
                "character_name": char.character_name,
                "character_portrait": char.portrait_url,
                "expiry_time": expiry_time,
                "expiry_hours": expiry_hours,
                "isk_day": isk_day,
                "highest_tier": highest_tier,
            })

    next_expiry_hours = _hours_until(next_expiry)
    char_count = len(characters)
    colony_count = len(colonies)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "account": account,
        "main_char": main_char,
        "characters": characters,
        "char_count": char_count,
        "colonies": colonies,
        "colony_count": colony_count,
        "planet_type_colors": planet_type_colors,
        "total_isk_day": total_isk_day,
        "next_expiry": next_expiry,
        "next_expiry_hours": next_expiry_hours,
        "next_expiry_char": next_expiry_char,
    })


@router.get("/characters", response_class=HTMLResponse)
def characters_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    db.refresh(account)
    characters = db.query(Character).filter(
        Character.account_id == account.id
    ).all()

    return templates.TemplateResponse("characters.html", {
        "request": request,
        "account": account,
        "characters": characters,
    })
