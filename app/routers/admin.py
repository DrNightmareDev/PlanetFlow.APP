from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.dependencies import require_admin, require_account, require_manager_or_admin
from app.i18n import get_translation_rows, save_translation, SUPPORTED_LANGUAGES
from app.models import Account, Character, AccessPolicy, AccessPolicyEntry, PageAccessSetting
from app.page_access import get_access_settings_map, get_page_definitions
from app.session import create_session, create_impersonate_session, read_session
from app.templates_env import templates

router = APIRouter(prefix="/admin", tags=["admin"])


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

    accounts_data = []
    for acc in accounts:
        chars = chars_by_account.get(acc.id, [])
        main = _mains_by_id.get(acc.main_character_id) if acc.main_character_id else None
        accounts_data.append({
            "account": acc,
            "characters": chars,
            "main": main,
            "char_count": len(chars),
            "colony_count": colony_counts.get(acc.id, 0),
            "is_ceo": False,
            "is_director": False,
            "roles_scope_missing": False,
        })

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


@router.get("/toggle-admin/{target_account_id}")
def toggle_admin(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    # Administrator kann nur von sich selbst entfernt werden
    if target.is_owner and not account.is_owner:
        raise HTTPException(status_code=403, detail="Manager-Rechte des Administrators koennen nur vom Administrator selbst geaendert werden")

    # Nicht-Administratoren koennen sich selbst nicht entfernen
    if target_account_id == account.id and not account.is_owner:
        raise HTTPException(status_code=400, detail="Du kannst deine eigenen Manager-Rechte nicht entziehen")

    target.is_admin = not target.is_admin
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/toggle-director/{target_account_id}")
def toggle_director(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")
    target.is_director = not target.is_director
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
        raise HTTPException(status_code=403, detail="Der Administrator-Account kann nicht geloescht werden")

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


@router.get("/impersonate/{target_account_id}")
def impersonate(
    target_account_id: int,
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
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


@router.get("/impersonate-exit")
def impersonate_exit(request: Request, db: Session = Depends(get_db)):
    session = read_session(request)
    real_owner_id = session.get("real_owner_id") if session else None
    if not real_owner_id:
        return RedirectResponse(url="/dashboard", status_code=302)
    owner = db.query(Account).filter(Account.id == real_owner_id).first()
    if not owner or not owner.is_owner:
        return RedirectResponse(url="/dashboard", status_code=302)
    response = RedirectResponse(url="/admin", status_code=302)
    create_session(response, account_id=real_owner_id)
    return response


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


@router.post("/page-access")
async def update_page_access(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_owner(account)
    form = await request.form()
    page_key = (form.get("page_key") or "").strip()
    access_level = (form.get("access_level") or "").strip()
    page = next((entry for entry in get_page_definitions() if entry.key == page_key), None)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    if page.admin_only:
        raise HTTPException(status_code=400, detail="Admin-only pages cannot be changed")
    if access_level not in ("none", "admin", "manager", "member"):
        raise HTTPException(status_code=400, detail="Invalid access level")

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
        BillingTransactionMatch, BillingWalletTransaction,
    )
    plans = db.query(BillingSubscriptionPlan).order_by(BillingSubscriptionPlan.id).all()
    tiers = db.query(BillingPricingTier).order_by(BillingPricingTier.scope, BillingPricingTier.min_members).all()
    receivers = db.query(BillingWalletReceiver).order_by(BillingWalletReceiver.id).all()
    codes = db.query(BillingBonusCode).order_by(BillingBonusCode.created_at.desc()).limit(50).all()
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
    return templates.TemplateResponse("admin/billing.html", {
        "request": request,
        "account": account,
        "plans": plans,
        "tiers": tiers,
        "receivers": receivers,
        "codes": codes,
        "grants": grants,
        "unmatched_txs": unmatched_txs,
        "audit_rows": audit_rows,
        "all_accounts": all_accounts,
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
    code = db.get(BillingBonusCode, code_id)
    if code:
        code.is_active = not code.is_active
        db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)


@router.post("/billing/subscription/grant", response_class=HTMLResponse)
def admin_billing_subscription_grant(
    request: Request,
    target_account_id: int = Form(...),
    subject_type: str = Form("account"),
    days: int = Form(...),
    note: str = Form(""),
    account=Depends(require_admin),
    db: Session = Depends(get_db),
):
    from decimal import Decimal
    from app.services.billing import extend_subscription
    extend_subscription(
        db,
        subject_type=subject_type,
        subject_id=target_account_id,
        plan_id=None,
        days=Decimal(days),
        source_type="manual_grant",
        note=note or f"Manual grant by admin {account.id}",
        actor_account_id=account.id,
    )
    db.commit()
    return RedirectResponse(url="/admin/billing", status_code=303)
