import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

_hasher = PasswordHasher()
_session_serializer = URLSafeTimedSerializer(settings.secret_key, salt="svcadmin-session")
_csrf_serializer = URLSafeTimedSerializer(settings.secret_key, salt="svcadmin-login-csrf")


class AuthRequired(Exception):
    pass


class PermissionDenied(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_session(username: str, role: str = "admin") -> str:
    return _session_serializer.dumps(
        {"u": username, "r": role, "csrf": secrets.token_hex(16)}
    )


def read_session(request: Request) -> dict | None:
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        return None
    try:
        data = _session_serializer.loads(raw, max_age=settings.session_max_age)
    except BadSignature:
        return None
    if isinstance(data, dict):
        data.setdefault("r", "admin")
    return data


def require_user(request: Request) -> dict:
    session = read_session(request)
    if not session:
        raise AuthRequired()
    return session


def require_admin(request: Request) -> dict:
    session = require_user(request)
    if session.get("r") != "admin":
        raise PermissionDenied()
    return session


def login_csrf_token() -> str:
    return _csrf_serializer.dumps("login")


def verify_login_csrf(token: str) -> bool:
    try:
        _csrf_serializer.loads(token or "", max_age=3600)
        return True
    except BadSignature:
        return False


def verify_csrf(request: Request, token: str) -> bool:
    session = read_session(request)
    if not session or not token:
        return False
    return hmac.compare_digest(session.get("csrf", ""), token)
