from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.dependencies import require_admin, require_account
from app.models import Account, Character, IskSnapshot, AccessPolicy, AccessPolicyEntry
from app.templates_env import templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _colony_count_per_account(db: Session) -> dict[int, int]:
    """Ermittelt die letzte bekannte Kolonienanzahl pro Account aus IskSnapshot."""
    # Für jeden Account: neuesten IskSnapshot holen
    from sqlalchemy import desc
    subq = (
        db.query(IskSnapshot.account_id, func.max(IskSnapshot.recorded_at).label("latest"))
        .group_by(IskSnapshot.account_id)
        .subquery()
    )
    rows = (
        db.query(IskSnapshot)
        .join(subq, (IskSnapshot.account_id == subq.c.account_id) &
                     (IskSnapshot.recorded_at == subq.c.latest))
        .all()
    )
    return {r.account_id: r.colony_count for r in rows}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    accounts = db.query(Account).all()
    colony_counts = _colony_count_per_account(db)

    total_accounts = len(accounts)
    total_chars = db.query(Character).count()
    total_admins = sum(1 for a in accounts if a.is_admin)
    total_colonies = sum(colony_counts.values())

    accounts_data = []
    for acc in accounts:
        chars = db.query(Character).filter(Character.account_id == acc.id).all()
        main = None
        if acc.main_character_id:
            main = db.query(Character).filter(Character.id == acc.main_character_id).first()
        accounts_data.append({
            "account": acc,
            "characters": chars,
            "main": main,
            "char_count": len(chars),
            "colony_count": colony_counts.get(acc.id, 0),
        })

    policy = db.get(AccessPolicy, 1)
    policy_entries = (
        db.query(AccessPolicyEntry)
        .filter_by(policy_id=1)
        .order_by(AccessPolicyEntry.entity_type, AccessPolicyEntry.entity_name)
        .all()
    )

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "account": account,
        "accounts_data": accounts_data,
        "total_accounts": total_accounts,
        "total_chars": total_chars,
        "total_admins": total_admins,
        "total_colonies": total_colonies,
        "policy": policy,
        "policy_entries": policy_entries,
    })


@router.get("/toggle-admin/{target_account_id}")
def toggle_admin(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    # Besitzer kann nur von sich selbst entfernt werden
    if target.is_owner and not account.is_owner:
        raise HTTPException(status_code=403, detail="Admin-Rechte des Besitzers können nur vom Besitzer selbst geändert werden")

    # Nicht-Besitzer können sich selbst nicht entfernen
    if target_account_id == account.id and not account.is_owner:
        raise HTTPException(status_code=400, detail="Du kannst deine eigenen Admin-Rechte nicht entziehen")

    target.is_admin = not target.is_admin
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/delete-account/{target_account_id}")
def delete_account(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    if target_account_id == account.id:
        raise HTTPException(status_code=400, detail="Du kannst deinen eigenen Account nicht löschen")

    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    if target.is_owner:
        raise HTTPException(status_code=403, detail="Der Besitzer-Account kann nicht gelöscht werden")

    db.delete(target)
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/delete-character/{character_id}")
def admin_delete_character(
    character_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    char = db.query(Character).filter(Character.id == character_id).first()
    if not char:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden")

    target_account = db.query(Account).filter(Account.id == char.account_id).first()
    if not target_account:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    # Main-Char eines fremden Accounts darf nicht gelöscht werden wenn es weitere Chars gibt
    if char.id == target_account.main_character_id:
        other_chars = db.query(Character).filter(
            Character.account_id == target_account.id,
            Character.id != char.id
        ).count()
        if other_chars > 0:
            raise HTTPException(status_code=400, detail="Main-Charakter kann nicht gelöscht werden solange Alts vorhanden sind")

    db.delete(char)
    # Falls es der letzte Char war, main_character_id zurücksetzen
    if char.id == target_account.main_character_id:
        target_account.main_character_id = None
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/set-main/{account_id}/{character_id}")
def admin_set_main(
    account_id: int,
    character_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    target_account = db.query(Account).filter(Account.id == account_id).first()
    if not target_account:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    char = db.query(Character).filter(
        Character.id == character_id,
        Character.account_id == account_id
    ).first()
    if not char:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden")

    target_account.main_character_id = char.id
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


# ── Zugangspolitik (nur Besitzer) ─────────────────────────────────────────────

def _require_owner(account):
    if not account.is_owner:
        raise HTTPException(status_code=403, detail="Nur der Besitzer kann die Zugangspolitik verwalten")


@router.post("/access-policy/mode")
async def set_access_policy_mode(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_owner(account)
    form = await request.form()
    mode = form.get("mode", "open")
    if mode not in ("open", "allowlist", "blocklist"):
        raise HTTPException(status_code=400, detail="Ungültiger Modus")
    policy = db.get(AccessPolicy, 1)
    policy.mode = mode
    db.commit()
    return RedirectResponse(url="/admin#access-policy", status_code=302)


@router.post("/access-policy/add")
async def add_access_policy_entry(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_owner(account)
    form = await request.form()
    entity_type = form.get("entity_type", "")
    entity_id_raw = form.get("entity_id", "")
    entity_name = (form.get("entity_name") or "").strip()

    if entity_type not in ("corporation", "alliance"):
        raise HTTPException(status_code=400, detail="Ungültiger Typ")
    try:
        entity_id = int(entity_id_raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="entity_id muss eine Ganzzahl sein")

    existing = db.query(AccessPolicyEntry).filter_by(
        policy_id=1, entity_type=entity_type, entity_id=entity_id
    ).first()
    if not existing:
        db.add(AccessPolicyEntry(
            policy_id=1,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name or None,
        ))
        db.commit()
    return RedirectResponse(url="/admin#access-policy", status_code=302)


@router.get("/access-policy/remove/{entry_id}")
def remove_access_policy_entry(
    entry_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_owner(account)
    entry = db.query(AccessPolicyEntry).filter_by(id=entry_id, policy_id=1).first()
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse(url="/admin#access-policy", status_code=302)


@router.get("/access-policy/search")
def search_access_policy_entity(
    q: str = "",
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Sucht Corps/Allianzen via ESI. Unterstützt direkte ID-Eingabe und Namenssuche."""
    _require_owner(account)
    q = q.strip()
    if not q:
        return JSONResponse({"corporations": [], "alliances": []})

    from app.esi import (
        search_entities, universe_ids,
        get_corporation_info, get_alliance_info, ensure_valid_token,
    )

    corps: list[dict] = []
    alliances: list[dict] = []
    seen_corp_ids: set[int] = set()
    seen_alliance_ids: set[int] = set()

    def add_corp(cid: int):
        if cid in seen_corp_ids:
            return
        seen_corp_ids.add(cid)
        try:
            info = get_corporation_info(cid)
            if info.get("name"):
                corps.append({"id": cid, "name": info["name"]})
        except Exception:
            pass

    def add_alliance(aid: int):
        if aid in seen_alliance_ids:
            return
        seen_alliance_ids.add(aid)
        try:
            info = get_alliance_info(aid)
            if info.get("name"):
                alliances.append({"id": aid, "name": info["name"]})
        except Exception:
            pass

    # 1) Direkte ID-Eingabe (z.B. von zkillboard kopiert)
    if q.isdigit():
        entity_id = int(q)
        add_corp(entity_id)
        add_alliance(entity_id)
        return JSONResponse({"corporations": corps, "alliances": alliances})

    # 2) Exakte Namensauflösung via /universe/ids/ (kein Auth nötig)
    resolved = universe_ids([q])
    for entry in resolved.get("corporations", []):
        add_corp(entry["id"])
    for entry in resolved.get("alliances", []):
        add_alliance(entry["id"])

    # 3) Fuzzy-Suche via character search (benötigt Auth-Token)
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first()
    if main_char:
        token = ensure_valid_token(main_char, db)
        result = search_entities(main_char.eve_character_id, token, q)
        for cid in result.get("corporation", [])[:8]:
            add_corp(cid)
        for aid in result.get("alliance", [])[:8]:
            add_alliance(aid)

    return JSONResponse({"corporations": corps[:10], "alliances": alliances[:10]})
