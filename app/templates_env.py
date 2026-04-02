import json
import time
from fastapi.templating import Jinja2Templates
from app.i18n import t, current_lang, client_i18n, SUPPORTED_LANGUAGES

# Bumped on every server start → busts browser cache for static JS/CSS
_STATIC_VERSION = str(int(time.time()))


def format_isk(value: float) -> str:
    if not value:
        return "0 ISK"
    if value >= 1_000_000_000:
        return f"{value/1_000_000_000:.2f} B ISK"
    if value >= 1_000_000:
        return f"{value/1_000_000:.2f} M ISK"
    if value >= 1_000:
        return f"{value/1_000:.1f} K ISK"
    return f"{value:.0f} ISK"


def format_expiry(hours: float) -> str:
    """Formatiert Stunden als 'Xd Yh Zm' oder 'Yh Zm'."""
    if hours < 0:
        return "Abgelaufen"
    h = int(hours)
    mins = int((hours % 1) * 60)
    if h >= 24:
        days = h // 24
        rem_h = h % 24
        return f"{days}d {rem_h}h {mins}m"
    return f"{h}h {mins}m"


def account_can_access_corp_nav(account) -> bool:
    if not account:
        return False
    return bool(getattr(account, "is_owner", False) or getattr(account, "is_admin", False))


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["format_isk"] = format_isk
templates.env.filters["format_expiry"] = format_expiry
templates.env.filters["from_json"] = lambda s: json.loads(s) if s else {}
templates.env.globals["static_version"] = _STATIC_VERSION
templates.env.globals["account_can_access_corp_nav"] = account_can_access_corp_nav
templates.env.globals["t"] = t
templates.env.globals["current_lang"] = current_lang
templates.env.globals["client_i18n"] = client_i18n
templates.env.globals["supported_languages"] = SUPPORTED_LANGUAGES
