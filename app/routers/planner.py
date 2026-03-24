from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.i18n import get_language_from_request, translate, translate_type_name
from app.market import PI_TYPE_IDS
from app.models import PiFavorite
from app.pi_data import (
    P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4,
    PLANET_RESOURCES, PLANET_TYPE_COLORS,
    ALL_P1, ALL_P2, ALL_P3, ALL_P4,
)
from app.templates_env import templates
from app import sde

router = APIRouter(prefix="/planner", tags=["planner"])


def _resolve_type_id(name: str) -> int | None:
    return PI_TYPE_IDS.get(name) or sde.find_type_id_by_name(name)


def _build_product_labels(lang: str) -> dict[str, str]:
    names = set(P0_TO_P1.keys()) | set(P0_TO_P1.values()) | set(ALL_P1) | set(ALL_P2) | set(ALL_P3) | set(ALL_P4)
    names |= {item for values in P1_TO_P2.values() for item in values}
    names |= {item for values in P2_TO_P3.values() for item in values}
    names |= {item for values in P3_TO_P4.values() for item in values}
    labels: dict[str, str] = {}
    for name in names:
        labels[name] = translate_type_name(_resolve_type_id(name), fallback=name, lang=lang)
    return labels


def _build_planet_type_labels(lang: str) -> dict[str, str]:
    return {name: translate(f"planet_type.{name}", lang=lang, default=name) for name in PLANET_TYPE_COLORS}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def planner_page(request: Request, account=Depends(require_account)):
    lang = get_language_from_request(request)
    product_labels = _build_product_labels(lang)
    planet_type_labels = _build_planet_type_labels(lang)

    def build_tier(names, tier):
        products = []
        for name in names:
            products.append({
                "name": name,
                "display_name": product_labels.get(name, name),
                "tier": tier,
                "type_id": _resolve_type_id(name),
            })
        return sorted(products, key=lambda item: item["display_name"].casefold())

    all_products = (
        build_tier(ALL_P1, "P1") +
        build_tier(ALL_P2, "P2") +
        build_tier(ALL_P3, "P3") +
        build_tier(ALL_P4, "P4")
    )
    return templates.TemplateResponse("planner.html", {
        "request": request,
        "account": account,
        "p0_to_p1": P0_TO_P1,
        "p1_to_p2": P1_TO_P2,
        "p2_to_p3": P2_TO_P3,
        "p3_to_p4": P3_TO_P4,
        "planet_resources": PLANET_RESOURCES,
        "planet_type_colors": PLANET_TYPE_COLORS,
        "all_products": all_products,
        "product_labels": product_labels,
        "planet_type_labels": planet_type_labels,
    })


@router.get("/favorites")
def get_favorites(account=Depends(require_account), db: Session = Depends(get_db)):
    favs = db.query(PiFavorite).filter(PiFavorite.account_id == account.id).all()
    return JSONResponse([f.product_name for f in favs])


class FavoriteToggle(BaseModel):
    product_name: str


@router.post("/favorites/toggle")
def toggle_favorite(
    body: FavoriteToggle,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    existing = db.query(PiFavorite).filter(
        PiFavorite.account_id == account.id,
        PiFavorite.product_name == body.product_name,
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        return JSONResponse({"favorited": False})
    db.add(PiFavorite(account_id=account.id, product_name=body.product_name))
    db.commit()
    return JSONResponse({"favorited": True})
