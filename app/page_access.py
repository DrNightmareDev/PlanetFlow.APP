from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import PageAccessSetting, SiteSettings


@dataclass(frozen=True)
class PageDefinition:
    key: str
    label: str
    path_prefix: str
    default_access: str = "member"
    admin_only: bool = False
    nav_group: str = "main"


PAGE_DEFINITIONS: tuple[PageDefinition, ...] = (
    PageDefinition("dashboard_characters", "Characters", "/dashboard/characters", "member,paid"),
    PageDefinition("dashboard", "Dashboard", "/dashboard", "member,paid"),
    PageDefinition("skyhook", "Skyhooks", "/skyhook", "member,paid"),
    PageDefinition("planner", "Planner", "/planner", "member,paid"),
    PageDefinition("inventory", "Inventory", "/inventory", "admin"),
    PageDefinition("colony_plan", "Colony Plan", "/colony-plan", "member,paid"),
    PageDefinition("pi_templates", "PI Templates", "/templates", "member,paid"),
    PageDefinition("hauling", "Hauling", "/hauling", "manager,paid"),
    PageDefinition("system", "System Analysis", "/system", "member,paid"),
    PageDefinition("market", "Market", "/market", "member,paid"),
    PageDefinition("intel_map", "Combat Intel Map", "/intel/map", "manager,paid"),
    PageDefinition("admin", "Admin Panel", "/admin", "admin"),
    PageDefinition("director", "Director Panel", "/director", "director,paid"),
    PageDefinition("billing", "Subscription", "/billing", "member", nav_group="account"),
)

PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/",
    "/auth",
    "/static",
    "/health",
)

_DEFINITION_BY_KEY = {page.key: page for page in PAGE_DEFINITIONS}


def ensure_page_access_settings(db: Session) -> None:
    try:
        rows = db.query(PageAccessSetting).all()
    except Exception:
        db.rollback()
        return
    existing = {row.page_key for row in rows}
    existing_rows = {row.page_key: row for row in rows}
    created = False
    updated = False
    for page in PAGE_DEFINITIONS:
        if page.admin_only or page.key in existing:
            continue
        db.add(PageAccessSetting(page_key=page.key, access_level=page.default_access))
        created = True
    # Never allow /admin to drift away from explicit admin-only role gate.
    admin_row = existing_rows.get("admin")
    if admin_row is not None and (admin_row.access_level or "").strip() != "admin":
        admin_row.access_level = "admin"
        updated = True
    if created or updated:
        db.commit()


def get_page_definitions() -> tuple[PageDefinition, ...]:
    return PAGE_DEFINITIONS


def get_page_definition(page_key: str) -> PageDefinition | None:
    return _DEFINITION_BY_KEY.get(page_key)


def get_access_settings_map(db: Session) -> dict[str, str]:
    ensure_page_access_settings(db)
    try:
        rows = db.query(PageAccessSetting).all()
    except Exception:
        db.rollback()
        return {
            page.key: page.default_access
            for page in PAGE_DEFINITIONS
            if not page.admin_only
        }
    return {row.page_key: row.access_level for row in rows}


def get_subscription_badge_settings_map(db: Session) -> dict[str, bool]:
    ensure_page_access_settings(db)
    try:
        rows = db.query(PageAccessSetting).all()
    except Exception:
        db.rollback()
        return {page.key: False for page in PAGE_DEFINITIONS if not page.admin_only}
    return {row.page_key: bool(getattr(row, "show_subscription_badge", False)) for row in rows}


def get_effective_access_level(page_key: str, db: Session | None = None, settings_map: dict[str, str] | None = None) -> str | None:
    page = get_page_definition(page_key)
    if not page:
        return None
    if page.admin_only:
        return "admin"
    if settings_map is not None:
        return settings_map.get(page_key, page.default_access)
    if db is None:
        return page.default_access
    return get_access_settings_map(db).get(page_key, page.default_access)


def can_account_access_page(
    page_key: str,
    account,
    db: Session | None = None,
    settings_map: dict[str, str] | None = None,
    entitlement_map: dict[str, bool] | None = None,
) -> bool:
    """
    Return True if the account may access this page.

    access_level values (comma-separated):
      none      -> blocked for everyone (dominant)
      member    -> role gate: authenticated account
      manager   -> role gate: manager-level account
      fc        -> role gate: FC-level account
      director  -> role gate: director (or CEO) account
      paid      -> additional individual entitlement requirement
      admin     -> role gate: admin/owner

    Important:
      "paid" is additive. Example "director,paid" requires both
      director role and paid entitlement.
    """
    page = get_page_definition(page_key)
    if not page:
        return True
    access_level = get_effective_access_level(page_key, db=db, settings_map=settings_map)
    levels = {v.strip() for v in (access_level or "").split(",") if v.strip()}

    if "none" in levels:
        return False
    if account is None:
        return False
    if page.admin_only:
        return False

    def _is_ceo() -> bool:
        if db is None:
            return False
        try:
            from app.models import Character
            from app.esi import get_corporation_info

            chars = db.query(Character).filter(Character.account_id == account.id).all()
            corp_ids = {c.corporation_id for c in chars if c.corporation_id}
            char_eve_ids = {c.eve_character_id for c in chars}
            for corp_id in corp_ids:
                info = get_corporation_info(corp_id)
                if info.get("ceo_id") in char_eve_ids:
                    return True
        except Exception:
            return False
        return False

    def _effective_roles() -> set[str]:
        roles = {"member"}
        # Admin accounts keep full page-role coverage.
        # Owner status alone must not auto-grant manager/fc/director page roles.
        if bool(getattr(account, "is_admin", False)):
            roles.update({"admin", "manager", "fc", "director"})
            return roles
        if bool(getattr(account, "is_owner", False)):
            roles.add("admin")
        if bool(getattr(account, "is_corp_manager", False)):
            roles.add("manager")
        if bool(getattr(account, "is_fc", False)):
            roles.add("fc")
        if bool(getattr(account, "is_director", False)) or _is_ceo():
            roles.add("director")
        return roles

    def _has_paid_access() -> bool:
        if bool(getattr(account, "is_admin", False)):
            return True
        if db is not None:
            from app.services.entitlements import _resolve_page_entitlement

            # Use live DB resolution for correctness.
            # Cached maps may be stale right after permission-model changes.
            return _resolve_page_entitlement(db, account=account, page_key=page_key, access_level="paid")
        if entitlement_map is not None:
            return entitlement_map.get(page_key, False)
        return False

    role_tokens = {"member", "manager", "fc", "director", "admin"}
    required_roles = levels & role_tokens
    requires_paid = "paid" in levels

    # No role token means admin-only by policy.
    # In this mode, paid is ignored because access is already restricted to admin/owner.
    if not required_roles:
        required_roles = {"admin"}
        requires_paid = False

    if not (_effective_roles() & required_roles):
        return False
    if requires_paid and not _has_paid_access():
        return False
    return True


def get_page_visibility(
    account,
    db: Session | None = None,
    settings_map: dict[str, str] | None = None,
    entitlement_map: dict[str, bool] | None = None,
) -> dict[str, bool]:
    return {
        page.key: can_account_access_page(
            page.key, account, db=db, settings_map=settings_map, entitlement_map=entitlement_map
        )
        for page in PAGE_DEFINITIONS
    }


def match_page_for_path(path: str) -> PageDefinition | None:
    if path == "/":
        return None
    for page in PAGE_DEFINITIONS:
        prefix = page.path_prefix
        if path == prefix or path.startswith(prefix + "/"):
            return page
    return None


def get_billing_enabled(db: Session) -> bool:
    """Returns True if the billing system is enabled site-wide."""
    try:
        row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
        return bool(row.billing_enabled) if row else False
    except Exception:
        db.rollback()
        return False


def is_public_path(path: str) -> bool:
    if path == "/":
        return True
    for prefix in PUBLIC_PATH_PREFIXES:
        if prefix != "/" and (path == prefix or path.startswith(prefix + "/")):
            return True
    return False
