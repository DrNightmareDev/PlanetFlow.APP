from fastapi.templating import Jinja2Templates


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


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["format_isk"] = format_isk
templates.env.filters["format_expiry"] = format_expiry
