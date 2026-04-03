import secrets

from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from app.config import get_settings

settings = get_settings()

COOKIE_NAME = "planetflow_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 Tage


def get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="planetflow-session")


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
    response.delete_cookie(key=COOKIE_NAME, samesite="lax", secure=settings.cookie_secure)


CSRF_COOKIE_NAME = "planetflow_csrf"
CSRF_MAX_AGE = 60 * 60 * 8  # 8 hours


def get_csrf_token(request: Request) -> str:
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
        # Store on request state so the response middleware can set the cookie
        request.state.csrf_token_new = token
    else:
        request.state.csrf_token_new = None
    return token


def set_csrf_cookie_if_needed(request: Request, response: Response) -> None:
    token = getattr(request.state, "csrf_token_new", None)
    if token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=token,
            max_age=CSRF_MAX_AGE,
            httponly=False,  # needs to be readable by the CSRF check
            samesite="lax",
            secure=settings.cookie_secure,
        )


def validate_csrf(request: Request, token: str) -> None:
    expected = request.cookies.get(CSRF_COOKIE_NAME)
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
