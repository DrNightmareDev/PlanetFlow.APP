from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, Response
from app.config import get_settings

settings = get_settings()

COOKIE_NAME = "eve_pi_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 Tage


def get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="eve-pi-session")


def create_impersonate_session(response: Response, target_id: int, real_owner_id: int) -> None:
    s = get_serializer()
    token = s.dumps({"account_id": target_id, "real_owner_id": real_owner_id})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def create_session(response: Response, account_id: int) -> None:
    s = get_serializer()
    token = s.dumps({"account_id": account_id})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def read_session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    s = get_serializer()
    try:
        data = s.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


def clear_session(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, samesite="lax", secure=not settings.debug)
