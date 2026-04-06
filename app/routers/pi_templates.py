from __future__ import annotations

import json
import logging
import time
import urllib.error
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.models import PlanetTemplate
from app.session import validate_csrf_header
from app.templates_env import templates

logger = logging.getLogger(__name__)
_SEED_TOTAL_TIMEOUT_SECONDS = 60

router = APIRouter(prefix="/templates", tags=["pi-templates"])

# Building family → canonical display name (for grouping in the legend)
_BUILDING_FAMILY_NAME: dict[str, str] = {
    "command_center":   "Command Center",
    "launchpad":        "Launch Pad",
    "storage":          "Storage Facility",
    "ecu":              "Extractor Control Unit",
    "extractor_head":   "Extractor Head",
    "adv_industrial":   "Adv. Industrial Facility",
    "basic_industrial": "Basic Industrial Facility",
    "high_tech":        "High-Tech Production Plant",
}

# Type ID → building family (all planet-type variants — mirrors pi_canvas.js)
_TYPE_FAMILY: dict[int, str] = {
    # Command Centers
    **{tid: "command_center" for tid in [
        2254, 2524, 2525, 2533, 2534, 2549, 2550, 2551,
        2129,2130,2131,2132,2133,2134,2135,2136,2137,2138,
        2139,2140,2141,2142,2143,2144,2145,2146,2147,2148,
        2149,2150,2151,2152,2153,2154,2155,2156,2157,2158,
        2159,2160,2574,2576,2577,2578,2581,2582,2585,2586,
    ]},
    # Launch Pads
    **{tid: "launchpad" for tid in [2256,2542,2543,2544,2552,2555,2556,2557]},
    # Storage Facilities
    **{tid: "storage" for tid in [2257,2535,2536,2541,2558,2560,2561,2562]},
    # Extractor Control Units
    **{tid: "ecu" for tid in [2848,3060,3061,3062,3063,3064,3067,3068]},
    # Extractor Heads (template-internal)
    2481: "extractor_head",
    # Advanced Industrial Facilities
    **{tid: "adv_industrial" for tid in [2470,2472,2474,2480,2484,2485,2491,2494]},
    # Basic Industrial Facilities
    **{tid: "basic_industrial" for tid in [2469,2471,2473,2483,2490,2492,2493]},
    # High-Tech Production Plants
    **{tid: "high_tech" for tid in [2475,2482]},
}

# Planet type IDs → readable names
_PLANET_TYPES: dict[int, str] = {
    2014: "Temperate", 2015: "Ice",    2016: "Gas",
    2017: "Oceanic",   2018: "Barren", 2019: "Storm",
    2063: "Plasma",    13:   "Lava",
}


def _type_display_name(type_id: int | None) -> str:
    if type_id is None:
        return "Unknown"
    family = _TYPE_FAMILY.get(type_id)
    if family:
        return _BUILDING_FAMILY_NAME[family]
    # Fallback: try SDE
    try:
        from app import sde as _sde
        name = _sde.get_type_name(type_id, "en")
        if name:
            return name
    except Exception:
        pass
    return f"TypeID {type_id}"


def _parse_template_meta(layout_json: str) -> dict:
    """Extract display metadata from the raw layout JSON."""
    try:
        data = json.loads(layout_json)
    except Exception:
        return {}

    pins = data.get("P", [])
    building_counts: dict[str, int] = {}
    for pin in pins:
        t = pin.get("T")
        name = _type_display_name(t)
        building_counts[name] = building_counts.get(name, 0) + 1

    planet_type_id = data.get("Pln")
    planet_type = _PLANET_TYPES.get(planet_type_id, f"Type {planet_type_id}" if planet_type_id else "Unknown")

    return {
        "cmd_center_level": data.get("CmdCtrLv", "?"),
        "comment": data.get("Cmt", ""),
        "planet_type": planet_type,
        "pin_count": len(pins),
        "link_count": len(data.get("L", [])),
        "route_count": len(data.get("R", [])),
        "building_counts": building_counts,
    }


# ── Community seed sources ────────────────────────────────────────────────────
_SEED_SOURCES = [
    {
        "api_url": "https://api.github.com/repos/DalShooth/EVE_PI_Templates/contents/PlanetaryInteractionTemplates",
        "author": "DalShooth",
    },
    {
        "api_url": "https://api.github.com/repos/TheLegi0n-NBI/PlayingWithPP/contents",
        "author": "TheLegi0n-NBI",
    },
]


def _list_github_json_files(api_url: str, author: str) -> list[dict]:
    """Recursively list all PI template files under a GitHub Contents API URL.

    Accepts .json, .txt, and extensionless files (repos like TheLegi0n-NBI/PlayingWithPP
    use mixed conventions). Skips files named 'readme' (case-insensitive).
    """
    import urllib.request
    req = urllib.request.Request(api_url, headers={"User-Agent": "EVE-PI-Manager/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        entries: list[dict] = json.loads(resp.read().decode())
    results = []
    for entry in entries:
        name: str = entry.get("name", "")
        if entry.get("type") == "file":
            # Skip readme/license files
            base = name.lower().removesuffix(".json").removesuffix(".txt")
            if base in ("readme", "license", ""):
                continue
            # Accept .json, .txt, or no extension
            if name.endswith(".json") or name.endswith(".txt") or "." not in name:
                display_name = name.removesuffix(".json").removesuffix(".txt")
                results.append({
                    "name": display_name,
                    "download_url": entry.get("download_url", ""),
                    "html_url": entry.get("html_url", ""),
                    "author": author,
                })
        elif entry.get("type") == "dir":
            subdir_url = entry.get("url", "")
            if subdir_url:
                try:
                    results.extend(_list_github_json_files(subdir_url, author))
                except Exception as exc:
                    logger.warning("seed: failed to recurse into %s: %s", subdir_url, exc)
    return results


def _source_label(source_url: str | None) -> str:
    if not source_url:
        return ""
    if "TheLegi0n-NBI" in source_url:
        return "TheLegi0n-NBI"
    if "DalShooth" in source_url:
        return "DalShooth"
    return ""


def _guess_planet_type(name: str, layout_json: str) -> str | None:
    """Guess planet type from name or embedded comment."""
    for t in ("Barren", "Gas", "Oceanic", "Temperate", "Ice", "Storm", "Plasma", "Lava"):
        if t.lower() in name.lower():
            return t
    try:
        data = json.loads(layout_json)
        cmt = data.get("Cmt", "")
        for t in ("Barren", "Gas", "Oceanic", "Temperate", "Ice", "Storm", "Plasma", "Lava"):
            if t.lower() in cmt.lower():
                return t
        pln = data.get("Pln")
        if pln in _PLANET_TYPES:
            return _PLANET_TYPES[pln]
    except Exception:
        pass
    return None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def list_templates(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    community = db.query(PlanetTemplate).filter_by(is_community=True).order_by(PlanetTemplate.name).all()
    own = db.query(PlanetTemplate).filter_by(is_community=False, account_id=account.id).order_by(PlanetTemplate.name).all()

    def _enrich(tmpl: PlanetTemplate) -> dict:
        meta = _parse_template_meta(tmpl.layout_json)
        return {
            "id": tmpl.id,
            "name": tmpl.name,
            "description": tmpl.description,
            "planet_type": tmpl.planet_type or meta.get("planet_type", ""),
            "is_community": tmpl.is_community,
            "cmd_center_level": meta.get("cmd_center_level"),
            "pin_count": meta.get("pin_count", 0),
            "building_counts": meta.get("building_counts", {}),
            "layout_json": tmpl.layout_json,
            "source_label": _source_label(tmpl.source_url),
        }

    return templates.TemplateResponse("pi_templates.html", {
        "request": request,
        "account": account,
        "community_templates": [_enrich(t) for t in community],
        "own_templates": [_enrich(t) for t in own],
    })


@router.get("/{template_id}", response_class=HTMLResponse)
def template_detail(
    template_id: int,
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    tmpl = db.get(PlanetTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    if not tmpl.is_community and tmpl.account_id != account.id and not account.is_admin:
        raise HTTPException(status_code=403, detail="Not your template")

    meta = _parse_template_meta(tmpl.layout_json)
    return templates.TemplateResponse("pi_template_detail.html", {
        "request": request,
        "account": account,
        "tmpl": tmpl,
        "meta": meta,
        "layout_json": tmpl.layout_json,
    })


@router.get("/{template_id}/download")
def download_template(
    template_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    tmpl = db.get(PlanetTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    if not tmpl.is_community and tmpl.account_id != account.id and not account.is_admin:
        raise HTTPException(status_code=403, detail="Not your template")

    safe_name = "".join(c if c.isalnum() or c in "- _" else "_" for c in tmpl.name)
    return Response(
        content=tmpl.layout_json,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )


@router.post("/upload")
async def upload_template(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    validate_csrf_header(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None
    raw_json = body.get("layout_json") or ""

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        parsed = json.loads(raw_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if "P" not in parsed:
        raise HTTPException(status_code=400, detail="JSON does not look like a PI template (missing 'P' array)")

    planet_type = _guess_planet_type(name, raw_json)

    tmpl = PlanetTemplate(
        account_id=account.id,
        name=name,
        description=description,
        planet_type=planet_type,
        layout_json=raw_json,
        is_community=False,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return JSONResponse({"ok": True, "id": tmpl.id})


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    validate_csrf_header(request)
    tmpl = db.get(PlanetTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    if not tmpl.is_community and tmpl.account_id != account.id and not account.is_admin:
        raise HTTPException(status_code=403, detail="Not your template")
    if tmpl.is_community and not account.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can delete community templates")

    db.delete(tmpl)
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/admin/seed-preview")
def seed_preview(
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Return the GitHub file list without inserting — used by the seed modal."""
    if not account.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    # Pre-fetch all already-seeded source_urls in one query
    seeded_urls: set[str] = {
        row.source_url
        for row in db.query(PlanetTemplate.source_url)
        .filter(PlanetTemplate.is_community == True, PlanetTemplate.source_url != None)
        .all()
    }

    all_files: list[dict] = []
    errors: list[str] = []
    started_at = time.monotonic()
    for src in _SEED_SOURCES:
        if time.monotonic() - started_at >= _SEED_TOTAL_TIMEOUT_SECONDS:
            note = f"Seed preview stopped after {_SEED_TOTAL_TIMEOUT_SECONDS}s total timeout."
            logger.warning("seed-preview: %s", note)
            errors.append(note)
            break
        try:
            all_files.extend(_list_github_json_files(src["api_url"], src["author"]))
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"{src['author']}: timed out or unreachable ({exc})")
        except Exception as exc:
            errors.append(f"{src['author']}: {exc}")

    if not all_files and errors:
        raise HTTPException(status_code=502, detail="; ".join(errors))

    items = [
        {
            "name": f["name"],
            "url": f["download_url"],
            "html_url": f["html_url"],
            "author": f["author"],
            "already_seeded": f["download_url"] in seeded_urls,
        }
        for f in all_files
    ]

    new_count = sum(1 for i in items if not i["already_seeded"])
    return JSONResponse({"templates": items, "new_count": new_count, "errors": errors})


@router.post("/admin/seed")
async def seed_community_templates(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Fetch all templates from all seed sources on GitHub and seed them as community templates."""
    if not account.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    validate_csrf_header(request)

    import urllib.request

    all_files: list[dict] = []
    started_at = time.monotonic()
    for src in _SEED_SOURCES:
        if time.monotonic() - started_at >= _SEED_TOTAL_TIMEOUT_SECONDS:
            logger.warning("seed: stopping source listing after %ss total timeout", _SEED_TOTAL_TIMEOUT_SECONDS)
            break
        try:
            all_files.extend(_list_github_json_files(src["api_url"], src["author"]))
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("seed: source %s timed out or was unreachable: %s", src["api_url"], exc)
        except Exception as exc:
            logger.warning("seed: failed to list %s: %s", src["api_url"], exc)

    inserted = 0
    skipped = 0
    errors = 0

    for f in all_files:
        download_url = f["download_url"]
        raw_name = f["name"]

        existing = db.query(PlanetTemplate).filter_by(
            is_community=True, source_url=download_url
        ).first()
        if existing:
            skipped += 1
            continue

        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "EVE-PI-Manager/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_json = resp.read().decode("utf-8")
            parsed = json.loads(raw_json)
        except Exception as exc:
            logger.warning("seed: failed to fetch %s: %s", download_url, exc)
            errors += 1
            continue

        if "P" not in parsed:
            logger.debug("seed: skipping %s — not a PI template (no 'P' array)", download_url)
            skipped += 1
            continue

        planet_type = _guess_planet_type(raw_name, raw_json)
        description = parsed.get("Cmt") or None

        db.add(PlanetTemplate(
            account_id=None,
            name=raw_name,
            description=description,
            planet_type=planet_type,
            layout_json=raw_json,
            is_community=True,
            source_url=download_url,
        ))
        inserted += 1

    db.commit()
    return JSONResponse({"ok": True, "inserted": inserted, "skipped": skipped, "errors": errors})
