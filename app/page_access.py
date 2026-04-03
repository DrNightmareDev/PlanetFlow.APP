from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import PageAccessSetting


@dataclass(frozen=True)
class PageDefinition:
    key: str
    label: str
    path_prefix: str
    default_access: str = "member"
    admin_only: bool = False
    nav_group: str = "main"


PAGE_DEFINITIONS: tuple[PageDefinition, ...] = (
    PageDefinition("dashboard_characters", "Characters", "/dashboard/characters", "member"),
    PageDefinition("dashboard", "Dashboard", "/dashboard", "member"),
    PageDefinition("skyhook", "Skyhooks", "/skyhook", "member"),
    PageDefinition("planner", "Planner", "/planner", "member"),
    PageDefinition("inventory", "Inventory", "/inventory", "admin"),
    PageDefinition("colony_plan", "Colony Plan", "/colony-plan", "member"),
    PageDefinition("pi_templates", "PI Templates", "/templates", "member"),
    PageDefinition("hauling", "Hauling", "/hauling", "manager"),
    PageDefinition("system", "System Analysis", "/system", "member"),
    PageDefinition("market", "Market", "/market", "member"),
    PageDefinition("intel_map", "Combat Intel Map", "/intel/map", "manager"),
    PageDefinition("admin", "Admin Panel", "/admin", "manager"),
    PageDefinition("director", "Director Panel", "/director", "director"),
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
        existing = {
            row.page_key
            for row in db.query(PageAccessSetting.page_key).all()
        }
    except Exception:
        db.rollback()
        return
    created = False
    for page in PAGE_DEFINITIONS:
        if page.admin_only or page.key in existing:
            continue
        db.add(PageAccessSetting(page_key=page.key, access_level=page.default_access))
        created = True
    if created:
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

    access_level values (may be comma-separated for multi-level):
      none      → blocked for everyone (dominant)
      member    → all authenticated accounts
      manager   → admin/owner only
      director  → director, manager role, FC role, or CEO
      paid      → requires active subscription, grant, or bonus code
      admin     → admin-only page (page.admin_only)
    """
    page = get_page_definition(page_key)
    if not page:
        return True
    access_level = get_effective_access_level(page_key, db=db, settings_map=settings_map)

    # Parse multi-value access levels
    levels = {v.strip() for v in access_level.split(",") if v.strip()}

    # "none" is dominant
    if "none" in levels:
        return False
    if account is None:
        return False
    if page.admin_only:
        return False
    if "admin" in levels and len(levels) == 1:
        return False

    # Owner bypass applies to all non-admin pages
    if bool(getattr(account, "is_owner", False)):
        return True

    # Helper to check director-level access
    def _has_director_access() -> bool:
        if getattr(account, "is_director", False):
            return True
        if getattr(account, "is_corp_manager", False):
            return True
        if getattr(account, "is_fc", False):
            return True
        if db is not None:
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
                pass
        return False

    def _has_paid_access() -> bool:
        if bool(getattr(account, "is_admin", False)):
            return True
        if entitlement_map is not None:
            return entitlement_map.get(page_key, False)
        if db is not None:
            from app.services.entitlements import get_cached_page_entitlements, _resolve_page_entitlement
            cached = get_cached_page_entitlements(db, account_id=account.id)
            if cached is not None:
                return cached.get(page_key, False)
            return _resolve_page_entitlement(db, account=account, page_key=page_key, access_level="paid")
        return False

    # Check each level — access granted if ANY level matches
    if "member" in levels:
        return True
    if "manager" in levels and bool(getattr(account, "is_admin", False)):
        return True
    if "director" in levels and _has_director_access():
        return True
    if "paid" in levels and _has_paid_access():
        return True
    if "admin" in levels and bool(getattr(account, "is_admin", False)):
        return True

    return False


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


def is_public_path(path: str) -> bool:
    if path == "/":
        return True
    for prefix in PUBLIC_PATH_PREFIXES:
        if prefix != "/" and (path == prefix or path.startswith(prefix + "/")):
            return True
    return False
