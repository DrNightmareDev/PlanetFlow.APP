from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.dependencies import require_admin, require_account, require_manager_or_admin
from app.i18n import get_translation_rows, save_translation, reseed_translations, SUPPORTED_LANGUAGES
from app.models import Account, Character, AccessPolicy, AccessPolicyEntry, PageAccessSetting
from app.page_access import get_access_settings_map, get_page_definitions
from app.session import create_impersonate_session, create_session, read_session, validate_csrf
from app.templates_env import templates

router = APIRouter(prefix="/admin", tags=["admin"])

# Minimum scope required for colony data
_REQUIRED_SCOPE = "esi-planets.manage_planets.v1"
_CACHE_STALE_HOURS = 2.0


def _char_scope_ok(char: Character) -> bool:
    """True if the character has the planet-management scope."""
    scopes = char.scopes or ""
    return _REQUIRED_SCOPE in scopes


def _account_data_status(acc: Account, chars: list, cache_fetched_at) -> dict:
    """
    Returns a status dict for an account's data completeness:
      status: 'ok' | 'stale' | 'empty' | 'error' | 'no_token'
      issues: list of human-readable problem strings
    """
    issues = []
    active = [c for c in chars if not getattr(c, "vacation_mode", False)]

    if not active:
        return {"status": "ok", "issues": []}

    # Token / scope issues
    no_token = [c for c in active if not c.refresh_token]
    scope_missing = [c for c in active if c.refresh_token and not _char_scope_ok(c)]
    esi_errors = [c for c in active if (c.esi_consecutive_errors or 0) >= 3]

    for c in no_token:
        issues.append(f"{c.character_name}: kein Refresh-Token")
    for c in scope_missing:
        issues.append(f"{c.character_name}: Scope fehlt ({_REQUIRED_SCOPE})")
    for c in esi_errors:
        issues.append(f"{c.character_name}: {c.esi_consecutive_errors} ESI-Fehler")

    # Cache completeness
    if cache_fetched_at is None:
        issues.append("Kein Cache vorhanden")
        status = "empty"
    else:
        if cache_fetched_at.tzinfo is None:
            cache_fetched_at = cache_fetched_at.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - cache_fetched_at).total_seconds() / 3600.0
        if age_h > _CACHE_STALE_HOURS:
            issues.append(f"Cache veraltet ({age_h:.1f}h)")
            status = "stale"
        else:
            status = "ok"

    if esi_errors or scope_missing or no_token:
        status = "error" if not cache_fetched_at else ("error" if esi_errors else "stale")

    return {"status": status, "issues": issues}


def _colony_count_per_account(db: Session) -> dict[int, int]:
    """Summe der last_known_colony_count pro Account — schnelle Single-Query-Aggregation."""
    rows = (
        db.query(Character.account_id, func.sum(Character.last_known_colony_count))
        .group_by(Character.account_id)
        .all()
    )
    return {acc_id: int(count or 0) for acc_id, count in rows}



@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    account=Depends(require_manager_or_admin),
    db: Session = Depends(get_db)
):
    accounts = db.query(Account).all()
    colony_counts = _colony_count_per_account(db)

    total_accounts = len(accounts)
    total_admins = sum(1 for a in accounts if a.is_admin)
    total_colonies = sum(colony_counts.values())

    # Batch-load all characters (replaces N queries, one per account)
    all_chars = db.query(Character).all()
    total_chars = len(all_chars)
    chars_by_account: dict[int, list] = {}
    for char in all_chars:
        chars_by_account.setdefault(char.account_id, []).append(char)
    # Batch-load all main chars
    _main_ids = [acc.main_character_id for acc in accounts if acc.main_character_id]
    _mains_by_id = {
        c.id: c for c in db.query(Character).filter(Character.id.in_(_main_ids)).all()
    } if _main_ids else {}

    # Batch-load dashboard cache timestamps
    from app.models import DashboardCache
    cache_rows = db.query(DashboardCache.account_id, DashboardCache.fetched_at).all()
    cache_fetched: dict[int, datetime] = {r.account_id: r.fetched_at for r in cache_rows}

    accounts_data = []
    for acc in accounts:
        chars = chars_by_account.get(acc.id, [])
        main = _mains_by_id.get(acc.main_character_id) if acc.main_character_id else None
        data_status = _account_data_status(acc, chars, cache_fetched.get(acc.id))
        accounts_data.append({
            "account": acc,
            "characters": chars,
            "main": main,
            "char_count": len(chars),
            "colony_count": colony_counts.get(acc.id, 0),
            "is_ceo": False,
            "is_director": False,
            "roles_scope_missing": False,
            "data_status": data_status["status"],
            "data_issues": data_status["issues"],
            "char_scope_ok": {c.id: _char_scope_ok(c) for c in chars},
            "cache_fetched_at": cache_fetched.get(acc.id),
        })

    # Auto-trigger background refresh for accounts with missing/stale cache and valid scopes
    try:
        from app.tasks import refresh_account_task
        for entry in accounts_data:
            if entry["data_status"] in ("empty", "stale"):
                acc_obj = entry["account"]
                active_with_scope = [
                    c for c in entry["characters"]
                    if not getattr(c, "vacation_mode", False)
                    and c.refresh_token
                    and _char_scope_ok(c)
                    and (c.esi_consecutive_errors or 0) < 3
                ]
                if active_with_scope:
                    refresh_account_task.apply_async((acc_obj.id,), countdown=2)
    except Exception:
        pass  # Celery not available

    policy = db.get(AccessPolicy, 1)
    policy_entries = (
        db.query(AccessPolicyEntry)
        .filter_by(policy_id=1)
        .order_by(AccessPolicyEntry.entity_type, AccessPolicyEntry.entity_name)
        .all()
    )
    page_access_map = get_access_settings_map(db)
    page_access_rows = []
    for page in get_page_definitions():
        if page.admin_only:
            page_access_rows.append({
                "page": page,
                "access_level": "admin",
                "read_only": True,
            })
            continue
        page_access_rows.append({
            "page": page,
            "access_level": page_access_map.get(page.key, page.default_access),
            "read_only": False,
        })

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
        "page_access_rows": page_access_rows,
        "translation_rows": get_translation_rows(),
        "translation_languages": [lang for lang in SUPPORTED_LANGUAGES if lang not in ("en", "de")],
    })


@router.post("/i18n/update")
async def update_translation(
    request: Request,
    account=Depends(require_admin),
):
    payload = await request.json()
    key = (payload.get("key") or "").strip()
    updates = payload.get("updates") or {}
    if not key:
        raise HTTPException(status_code=400, detail="translation key missing")
    for locale, value in updates.items():
        if locale not in SUPPORTED_LANGUAGES:
            raise HTTPException(status_code=400, detail=f"unsupported locale: {locale}")
        try:
            save_translation(locale, key, str(value or ""))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"ok": True})


@router.post("/i18n/reseed")
async def reseed_translations_endpoint(
    account=Depends(require_admin),
):
    """Force-upsert all seed JSON translations into the DB (insert new + update changed)."""
    result = reseed_translations()
    return JSONResponse({"ok": True, **result})


@router.post("/reset-char-errors/{character_id}")
def reset_char_errors(
    character_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reset esi_consecutive_errors for a stuck character so it is retried immediately."""
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    char = db.get(Character, character_id)
    if not char:
        raise HTTPException(status_code=404)
    _logger.info(
        "admin: %s reset ESI errors for char %s (%d errors → 0)",
        account.id, char.character_name, char.esi_consecutive_errors or 0,
    )
    char.esi_consecutive_errors = 0
    char.colony_sync_issue = False
    db.commit()
    return JSONResponse({"ok": True, "character_name": char.character_name})


@router.post("/toggle-admin/{target_account_id}")
def toggle_admin(
    target_account_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
    if not account.is_owner:
        raise HTTPException(status_code=403, detail="Nur der Owner kann Admin-Rechte vergeben oder entziehen")

    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    # Owner-Status ist an EVE_OWNER_CHARACTER_ID gebunden – kein DB-Toggle möglich
    if target.is_owner:
        raise HTTPException(status_code=400, detail="Owner-Rechte können nicht über die UI geändert werden")

    target.is_admin = not target.is_admin
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/corps-list", response_class=JSONResponse)
def corps_list(
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return distinct corps present in the characters table, for the director modal."""
    from app.models import Character
    rows = (
        db.query(Character.corporation_id, Character.corporation_name)
        .filter(Character.corporation_id.isnot(None))
        .distinct()
        .order_by(Character.corporation_name)
        .all()
    )
    return JSONResponse([{"id": r.corporation_id, "name": r.corporation_name or str(r.corporation_id)} for r in rows])


@router.post("/set-director/{target_account_id}")
def set_director(
    target_account_id: int,
    request: Request,
    csrf_token: str = Form(...),
    corp_id: int = Form(...),
    corp_name: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")
    target.is_director = True
    target.director_corp_id = corp_id
    target.director_corp_name = corp_name
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/remove-director/{target_account_id}")
def remove_director(
    target_account_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")
    target.is_director = False
    target.director_corp_id = None
    target.director_corp_name = None
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/delete-account/{target_account_id}")
def delete_account(
    target_account_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
    if target_account_id == account.id:
        raise HTTPException(status_code=400, detail="Du kannst deinen eigenen Account nicht löschen")

    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    if target.is_owner:
        raise HTTPException(status_code=403, detail="Der Administrator-Account kann nicht geloescht werden")

    db.delete(target)
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/delete-character/{character_id}")
def admin_delete_character(
    character_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
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


@router.post("/impersonate/{target_account_id}")
def impersonate(
    target_account_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
    if not account.is_owner:
        raise HTTPException(status_code=403, detail="Nur der Administrator kann Accounts imitieren")
    if target_account_id == account.id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst imitieren")
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")
    response = RedirectResponse(url="/dashboard", status_code=302)
    create_impersonate_session(response, target_id=target_account_id, real_owner_id=account.id)
    return response


@router.post("/impersonate-exit")
def impersonate_exit(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    validate_csrf(request, csrf_token)
    session = read_session(request)
    real_owner_id = session.get("real_owner_id") if session else None
    if not real_owner_id:
        return RedirectResponse(url="/dashboard", status_code=302)
    from sqlalchemy.orm import joinedload
    owner = db.query(Account).options(joinedload(Account.characters)).filter(Account.id == real_owner_id).first()
    if not owner or not (owner.is_owner or owner.is_admin):
        return RedirectResponse(url="/dashboard", status_code=302)
    response = RedirectResponse(url="/admin", status_code=302)
    create_session(response, account_id=real_owner_id)
    return response


@router.post("/set-main/{account_id}/{character_id}")
def admin_set_main(
    account_id: int,
    character_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
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


# Zugangspolitik (nur Administrator)

def _require_owner(account):
    if not account.is_owner:
        raise HTTPException(status_code=403, detail="Nur der Administrator kann die Zugangspolitik verwalten")


@router.post("/access-policy/mode")
async def set_access_policy_mode(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token", ""))
    _require_owner(account)
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
    form = await request.form()
    validate_csrf(request, form.get("csrf_token", ""))
    _require_owner(account)
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


@router.post("/access-policy/remove/{entry_id}")
def remove_access_policy_entry(
    entry_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    _require_owner(account)
    entry = db.query(AccessPolicyEntry).filter_by(id=entry_id, policy_id=1).first()
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse(url="/admin#access-policy", status_code=302)


@router.post("/page-access")
async def update_page_access(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token", ""))
    _require_owner(account)
    page_key = (form.get("page_key") or "").strip()
    page = next((entry for entry in get_page_definitions() if entry.key == page_key), None)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    if page.admin_only:
        raise HTTPException(status_code=400, detail="Admin-only pages cannot be changed")

    _valid_levels = {"none", "member", "manager", "director", "paid"}
    selected = form.getlist("access_levels")
    # Filter to only valid values
    selected = [v for v in selected if v in _valid_levels]
    if not selected:
        selected = ["none"]
    # "none" is dominant
    if "none" in selected:
        access_level = "none"
    else:
        access_level = ",".join(sorted(set(selected)))

    row = db.get(PageAccessSetting, page_key)
    if row is None:
        row = PageAccessSetting(page_key=page_key, access_level=access_level)
        db.add(row)
    else:
        row.access_level = access_level
    db.commit()
    return RedirectResponse(url="/admin#page-access", status_code=302)


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


@router.post("/reload-account/{target_account_id}")
def admin_reload_account(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: force-refresh colony cache for any account (no corp restriction)."""
    from app.routers.dashboard import _build_dashboard_payload, _save_colony_cache, _dashboard_cache
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404)
    chars = db.query(Character).filter(Character.account_id == target_account_id).all()
    try:
        payload = _build_dashboard_payload(target, chars, db, price_mode=getattr(target, "price_mode", "sell"))
        _save_colony_cache(target_account_id, payload, db)
        _dashboard_cache[target_account_id] = payload
        return JSONResponse({"ok": True, "colony_count": payload["colony_count"]})
    except Exception as e:
        _logger.exception("admin_reload_account %s failed", target_account_id)
        return JSONResponse({"ok": False, "error": "Laden fehlgeschlagen"}, status_code=500)


@router.post("/trigger-refresh/{target_account_id}")
def admin_trigger_refresh(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: dispatch async Celery refresh for any account — returns immediately."""
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404)
    # Reset ESI errors on all chars so the refresh isn't blocked by backoff
    chars = db.query(Character).filter(Character.account_id == target_account_id).all()
    for char in chars:
        if (char.esi_consecutive_errors or 0) > 0:
            char.esi_consecutive_errors = 0
            char.colony_sync_issue = False
    db.commit()
    try:
        from app.tasks import refresh_account_task
        refresh_account_task.apply_async((target_account_id,), countdown=1)
        return JSONResponse({"ok": True, "queued": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Admin Billing ─────────────────────────────────────────────────────────────

@router.get("/billing", response_class=HTMLResponse)
def admin_billing(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import (
        BillingAuditLog, BillingBonusCode, BillingGrant,
        BillingPricingTier, BillingSubscriptionPlan, BillingWalletReceiver,
        BillingSubscriptionJoinCode, BillingTransactionMatch, BillingWalletTransaction,
    )
    plans = db.query(BillingSubscriptionPlan).order_by(BillingSubscriptionPlan.id).all()
    tiers = db.query(BillingPricingTier).order_by(BillingPricingTier.scope, BillingPricingTier.min_members).all()
    _WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"
    _raw_receivers = db.query(BillingWalletReceiver).order_by(BillingWalletReceiver.id).all()
    receivers = []
    for r in _raw_receivers:
        char = None
        if r.character_fk:
            char = db.get(Character, r.character_fk)
        if not char:
            char = db.query(Character).filter(Character.eve_character_id == r.eve_character_id).first()
        has_scope = bool(char and char.scopes and _WALLET_SCOPE in char.scopes)
        has_token = bool(char and char.refresh_token)
        receivers.append({"receiver": r, "has_scope": has_scope, "has_token": has_token, "char": char})
    codes = db.query(BillingBonusCode).order_by(BillingBonusCode.created_at.desc()).limit(50).all()
    join_codes = db.query(BillingSubscriptionJoinCode).order_by(BillingSubscriptionJoinCode.created_at.desc()).limit(50).all()
    grants = (
        db.query(BillingGrant, Account)
        .join(Account, Account.id == BillingGrant.account_id)
        .order_by(BillingGrant.created_at.desc())
        .limit(50)
        .all()
    )
    unmatched_txs = (
        db.query(BillingTransactionMatch, BillingWalletTransaction)
        .join(BillingWalletTransaction, BillingWalletTransaction.id == BillingTransactionMatch.transaction_id)
        .filter(BillingTransactionMatch.match_status == "unmatched")
        .order_by(BillingWalletTransaction.occurred_at.desc())
        .limit(20)
        .all()
    )
    audit_rows = (
        db.query(BillingAuditLog)
        .order_by(BillingAuditLog.created_at.desc())
        .limit(100)
        .all()
    )
    all_accounts = db.query(Account).order_by(Account.id).all()
    all_characters = db.query(Character).order_by(Character.character_name.asc()).all()
    corp_targets = (
        db.query(Character.corporation_id, Character.corporation_name)
        .filter(Character.corporation_id.isnot(None))
        .distinct()
        .order_by(Character.corporation_name.asc())
        .all()
    )
    alliance_targets = (
        db.query(Character.alliance_id, Character.alliance_name)
        .filter(Character.alliance_id.isnot(None))
        .distinct()
        .order_by(Character.alliance_name.asc())
        .all()
    )
    return templates.TemplateResponse("admin/billing.html", {
        "request": request,
        "account": account,
        "plans": plans,
        "tiers": tiers,
        "receivers": receivers,
        "codes": codes,
        "join_codes": join_codes,
        "grants": grants,
        "unmatched_txs": unmatched_txs,
        "audit_rows": audit_rows,
        "all_accounts": all_accounts,
        "all_characters": all_characters,
        "corp_targets": corp_targets,
        "alliance_targets": alliance_targets,
    })


@router.post("/billing/plan/save", response_class=HTMLResponse)
def admin_billing_plan_save(
    request: Request,
    scope: str = Form(...),
    display_name: str = Form(...),
    daily_price_isk: int = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingSubscriptionPlan
    plan = db.query(BillingSubscriptionPlan).filter(BillingSubscriptionPlan.scope == scope).first()
    if plan:
        plan.display_name = display_name
        plan.daily_price_isk = daily_price_isk
    else:
        db.add(BillingSubscriptionPlan(
            key=scope,
            scope=scope,
            display_name=display_name,
            daily_price_isk=daily_price_isk,
        ))
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/tier/save", response_class=HTMLResponse)
def admin_billing_tier_save(
    request: Request,
    scope: str = Form(...),
    min_members: int = Form(...),
    max_members: int | None = Form(None),
    daily_price_isk: int = Form(...),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingPricingTier
    tier = db.query(BillingPricingTier).filter(
        BillingPricingTier.scope == scope,
        BillingPricingTier.min_members == min_members,
    ).first()
    if tier:
        tier.max_members = max_members
        tier.daily_price_isk = daily_price_isk
    else:
        db.add(BillingPricingTier(
            scope=scope,
            min_members=min_members,
            max_members=max_members,
            daily_price_isk=daily_price_isk,
        ))
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/tier/delete/{tier_id}", response_class=HTMLResponse)
def admin_billing_tier_delete(
    request: Request,
    tier_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingPricingTier
    tier = db.get(BillingPricingTier, tier_id)
    if tier:
        db.delete(tier)
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/receiver/save", response_class=HTMLResponse)
def admin_billing_receiver_save(
    request: Request,
    eve_character_id: int = Form(...),
    character_name: str = Form(...),
    notes: str = Form(""),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingWalletReceiver
    existing = db.query(BillingWalletReceiver).filter(
        BillingWalletReceiver.eve_character_id == eve_character_id
    ).first()
    if existing:
        existing.character_name = character_name
        existing.notes = notes or None
        existing.is_active = True
    else:
        db.add(BillingWalletReceiver(
            eve_character_id=eve_character_id,
            character_name=character_name,
            notes=notes or None,
        ))
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/receiver/toggle/{receiver_id}", response_class=HTMLResponse)
def admin_billing_receiver_toggle(
    request: Request,
    receiver_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingWalletReceiver
    rec = db.get(BillingWalletReceiver, receiver_id)
    if rec:
        rec.is_active = not rec.is_active
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/grant/create", response_class=HTMLResponse)
def admin_billing_grant_create(
    request: Request,
    target_account_id: int = Form(...),
    scope_type: str = Form("global"),
    scope_key: str = Form(""),
    expires_days: int | None = Form(None),
    note: str = Form(""),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import timedelta
    from app.services.billing import create_grant
    expires_at = None
    if expires_days and expires_days > 0:
        from datetime import UTC, datetime
        expires_at = datetime.now(UTC) + timedelta(days=expires_days)
    create_grant(
        db,
        account_id=target_account_id,
        scope_type=scope_type,
        scope_key=scope_key or None,
        expires_at=expires_at,
        granted_by_account_id=account.id,
        note=note,
    )
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/grant/revoke/{grant_id}", response_class=HTMLResponse)
def admin_billing_grant_revoke(
    request: Request,
    grant_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingGrant
    from app.services.billing import revoke_grant
    grant = db.get(BillingGrant, grant_id)
    if grant:
        revoke_grant(db, grant=grant, actor_account_id=account.id)
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/code/create", response_class=HTMLResponse)
def admin_billing_code_create(
    request: Request,
    code: str = Form(...),
    reward_type: str = Form(...),
    reward_value: str = Form(...),
    max_redemptions: int | None = Form(None),
    expires_days: int | None = Form(None),
    note: str = Form(""),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import UTC, datetime, timedelta
    from app.models import BillingBonusCode
    existing = db.query(BillingBonusCode).filter(
        BillingBonusCode.code == code.upper().strip()
    ).first()
    if existing:
        return RedirectResponse(url="/admin/billing", status_code=303)
    expires_at = None
    if expires_days and expires_days > 0:
        expires_at = datetime.now(UTC) + timedelta(days=expires_days)
    db.add(BillingBonusCode(
        code=code.upper().strip(),
        reward_type=reward_type,
        reward_value=reward_value,
        max_redemptions=max_redemptions or None,
        expires_at=expires_at,
        created_by_account_id=account.id,
        note=note or None,
    ))
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/code/toggle/{code_id}", response_class=HTMLResponse)
def admin_billing_code_toggle(
    request: Request,
    code_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingBonusCode
    from app.services.billing import revoke_bonus_code
    code = db.get(BillingBonusCode, code_id)
    if code:
        turning_off = bool(code.is_active)
        code.is_active = not code.is_active
        if turning_off:
            # Deactivation should also remove already granted benefits to avoid stale entitlements.
            revoke_bonus_code(db, code=code, actor_account_id=account.id)
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/code/revoke/{code_id}", response_class=HTMLResponse)
def admin_billing_code_revoke(
    request: Request,
    code_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models import BillingBonusCode
    from app.services.billing import revoke_bonus_code

    code = db.get(BillingBonusCode, code_id)
    if code:
        revoke_bonus_code(db, code=code, actor_account_id=account.id)
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/join-code/revoke/{join_code_id}", response_class=HTMLResponse)
def admin_billing_join_code_revoke(
    request: Request,
    join_code_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import UTC, datetime
    from app.models import BillingSubscriptionJoinCode

    code = db.get(BillingSubscriptionJoinCode, join_code_id)
    if code and code.revoked_at is None:
        code.revoked_at = datetime.now(UTC)
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/subscription/grant", response_class=HTMLResponse)
def admin_billing_subscription_grant(
    request: Request,
    target_subject_id: int = Form(...),
    subject_type: str = Form("account"),
    days: int = Form(...),
    note: str = Form(""),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from decimal import Decimal
    from app.services.billing import extend_subscription, invalidate_subject_entitlements

    if days <= 0:
        return RedirectResponse(url="/admin/billing?msg=days_must_be_positive", status_code=303)

    final_subject_type = subject_type
    final_subject_id = int(target_subject_id)

    if subject_type == "character":
        char = db.get(Character, target_subject_id)
        if not char:
            return RedirectResponse(url="/admin/billing?msg=character_not_found", status_code=303)
        final_subject_type = "account"
        final_subject_id = int(char.account_id)
        if not note:
            note = f"Manual character grant via {char.character_name} ({char.eve_character_id})"
    elif subject_type in ("corporation", "alliance"):
        required = "CORP" if subject_type == "corporation" else "ALLIANCE"
        if required not in (note or "").upper():
            return RedirectResponse(url=f"/admin/billing?msg=reason_must_contain_{required.lower()}", status_code=303)
    elif subject_type != "account":
        return RedirectResponse(url="/admin/billing?msg=invalid_subject_type", status_code=303)

    extend_subscription(
        db,
        subject_type=final_subject_type,
        subject_id=final_subject_id,
        plan_id=None,
        days=Decimal(days),
        source_type="manual_grant",
        note=note or f"Manual grant by admin {account.id}",
        actor_account_id=account.id,
    )
    invalidate_subject_entitlements(db, subject_type=final_subject_type, subject_id=final_subject_id)
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)
