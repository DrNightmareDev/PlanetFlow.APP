"""
Billing router — user-facing subscription and bonus code pages.
Routes:
  GET  /billing              → subscription status, payment instructions, code form
  POST /billing/redeem       → redeem a bonus code
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.models import (
    Account,
    BillingBonusCodeRedemption,
    BillingGrant,
    BillingSubscriptionPeriod,
    BillingWalletReceiver,
    Character,
)
from app.templates_env import templates

router = APIRouter(prefix="/billing", tags=["billing"])


def _active_periods(db: Session, *, account: Account) -> list[dict]:
    """Return all currently active subscription periods that cover this account."""
    now = datetime.now(UTC)
    periods: list[dict] = []

    # Individual
    rows = (
        db.query(BillingSubscriptionPeriod)
        .filter(
            BillingSubscriptionPeriod.subject_type == "account",
            BillingSubscriptionPeriod.subject_id == account.id,
            BillingSubscriptionPeriod.ends_at >= now,
        )
        .order_by(BillingSubscriptionPeriod.ends_at.desc())
        .all()
    )
    for r in rows:
        periods.append({"scope": "Individual", "ends_at": r.ends_at, "source": r.source_type})

    # Corporation / Alliance via characters
    chars = db.query(Character).filter(Character.account_id == account.id).all()
    corp_ids = {c.corporation_id for c in chars if c.corporation_id}
    alliance_ids = {c.alliance_id for c in chars if c.alliance_id}

    for corp_id in corp_ids:
        rows = (
            db.query(BillingSubscriptionPeriod)
            .filter(
                BillingSubscriptionPeriod.subject_type == "corporation",
                BillingSubscriptionPeriod.subject_id == corp_id,
                BillingSubscriptionPeriod.ends_at >= now,
            )
            .order_by(BillingSubscriptionPeriod.ends_at.desc())
            .first()
        )
        if rows:
            corp_name = next((c.corporation_name for c in chars if c.corporation_id == corp_id), str(corp_id))
            periods.append({"scope": f"Corporation ({corp_name})", "ends_at": rows.ends_at, "source": rows.source_type})

    for alliance_id in alliance_ids:
        rows = (
            db.query(BillingSubscriptionPeriod)
            .filter(
                BillingSubscriptionPeriod.subject_type == "alliance",
                BillingSubscriptionPeriod.subject_id == alliance_id,
                BillingSubscriptionPeriod.ends_at >= now,
            )
            .order_by(BillingSubscriptionPeriod.ends_at.desc())
            .first()
        )
        if rows:
            alliance_name = next((c.alliance_name for c in chars if c.alliance_id == alliance_id), str(alliance_id))
            periods.append({"scope": f"Alliance ({alliance_name})", "ends_at": rows.ends_at, "source": rows.source_type})

    return periods


def _active_grants(db: Session, *, account: Account) -> list[BillingGrant]:
    now = datetime.now(UTC)
    return (
        db.query(BillingGrant)
        .filter(
            BillingGrant.account_id == account.id,
            BillingGrant.revoked_at.is_(None),
            BillingGrant.starts_at <= now,
            (BillingGrant.expires_at.is_(None)) | (BillingGrant.expires_at >= now),
        )
        .order_by(BillingGrant.expires_at.asc())
        .all()
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def billing_page(
    request: Request,
    msg: str = "",
    account: Account = Depends(require_account),
    db: Session = Depends(get_db),
):
    active_periods = _active_periods(db, account=account)
    active_grants = _active_grants(db, account=account)
    receivers = db.query(BillingWalletReceiver).filter(
        BillingWalletReceiver.is_active == True
    ).all()

    recent_redemptions = (
        db.query(BillingBonusCodeRedemption)
        .filter(BillingBonusCodeRedemption.account_id == account.id)
        .order_by(BillingBonusCodeRedemption.redeemed_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse("billing/index.html", {
        "request": request,
        "account": account,
        "active_periods": active_periods,
        "active_grants": active_grants,
        "receivers": receivers,
        "recent_redemptions": recent_redemptions,
        "now": datetime.now(UTC),
        "msg": msg,
    })


@router.post("/redeem", response_class=HTMLResponse)
def redeem_code(
    request: Request,
    code: str = Form(...),
    account: Account = Depends(require_account),
    db: Session = Depends(get_db),
):
    from app.services.billing import redeem_bonus_code

    success, message = redeem_bonus_code(db, code_value=code, account_id=account.id)
    if success:
        db.commit()
    from urllib.parse import quote
    return RedirectResponse(url=f"/billing?msg={quote(message)}", status_code=303)
