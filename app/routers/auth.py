import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AdminUser, utcnow
from ..security import (
    login_csrf_token,
    new_session,
    read_session,
    require_user,
    verify_login_csrf,
    verify_password,
)
from ..web import render
from .common import check_csrf, fv, redirect

router = APIRouter()

_failures: dict[str, list[float]] = {}
_WINDOW = 600
_MAX_FAILS = 8


def _is_locked(ip: str) -> bool:
    stamps = [t for t in _failures.get(ip, []) if time.time() - t < _WINDOW]
    _failures[ip] = stamps
    return len(stamps) >= _MAX_FAILS


@router.get("/login")
async def login_page(request: Request):
    if read_session(request):
        return RedirectResponse("/", status_code=303)
    return render(
        request,
        "login.html",
        csrf=login_csrf_token(),
        next=request.query_params.get("next", "/"),
    )


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    next_url = fv(form, "next") or "/"
    if not next_url.startswith("/"):
        next_url = "/"
    if not verify_login_csrf(fv(form, "csrf")):
        return render(request, "login.html", csrf=login_csrf_token(), next=next_url, err="会话已过期，请重试")

    ip = request.client.host if request.client else "-"
    if _is_locked(ip):
        return render(request, "login.html", csrf=login_csrf_token(), next=next_url, err="尝试过于频繁，请 10 分钟后再试")

    username = fv(form, "username")
    password = fv(form, "password")
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or not verify_password(user.password_hash, password):
        _failures.setdefault(ip, []).append(time.time())
        return render(request, "login.html", csrf=login_csrf_token(), next=next_url, err="用户名或密码错误")

    _failures.pop(ip, None)
    user.last_login = utcnow()
    db.commit()

    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        settings.session_cookie,
        new_session(username, user.role or "admin"),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=not settings.insecure_cookies,
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/", err="CSRF 校验失败")
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie)
    return response
