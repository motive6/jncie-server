import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import FtpUser
from ..security import require_user
from ..services import ftp
from ..services.base import unit_state
from ..services.store import audit, load, save
from ..web import render
from .common import check_csrf, fv, redirect

router = APIRouter()

USERNAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{1,31}$")


def _users(db: Session) -> list[FtpUser]:
    return list(db.scalars(select(FtpUser).order_by(FtpUser.username)))


@router.get("/ftp")
async def ftp_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    cfg = load(db, "ftp", ftp.DEFAULTS)
    edit_id = request.query_params.get("edit", "")
    edit_user = db.get(FtpUser, int(edit_id)) if edit_id.isdigit() else None
    return render(
        request,
        "ftp.html",
        active="ftp",
        cfg=cfg,
        users=_users(db),
        edit_user=edit_user,
        state=unit_state(ftp.UNIT),
        result=request.query_params.get("result", ""),
        ftp_uid=settings.ftp_uid,
    )


@router.post("/ftp/settings")
async def ftp_settings(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/ftp", err="CSRF 校验失败，请刷新后重试")

    passive = fv(form, "passive_ports").replace(" ", ":")
    if not re.match(r"^\d{2,5}:\d{2,5}$", passive):
        return redirect("/ftp", err="被动端口范围格式应为 39000:40000")
    try:
        max_clients = int(fv(form, "max_clients") or 20)
        max_per_ip = int(fv(form, "max_per_ip") or 5)
    except ValueError:
        return redirect("/ftp", err="连接数必须是整数")

    cfg = {
        "enabled": form.get("enabled") is not None,
        "tls": form.get("tls") is not None,
        "require_tls": form.get("require_tls") is not None,
        "passive_ports": passive,
        "max_clients": max_clients,
        "max_per_ip": max_per_ip,
    }
    result = ftp.apply_config(cfg, [u.username for u in _users(db)])
    if result.ok:
        save(db, "ftp", cfg)
    audit(db, require_user(request)["u"], "ftp", "保存并应用全局配置", result.text(), "ok" if result.ok else "fail")
    if not result.ok:
        return redirect("/ftp", err=result.text())
    return redirect("/ftp", msg="FTP 全局配置已应用")


@router.post("/ftp/users")
async def ftp_user_save(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/ftp", err="CSRF 校验失败，请刷新后重试")

    username = fv(form, "username").lower()
    password = fv(form, "password")
    user_id = fv(form, "id")
    if not USERNAME_RE.match(username):
        return redirect("/ftp", err="用户名需为 2-32 位小写字母、数字、下划线或短横线")

    try:
        quota = int(fv(form, "quota_mb") or 0)
        ul = int(fv(form, "ul_bandwidth") or 0)
        dl = int(fv(form, "dl_bandwidth") or 0)
    except ValueError:
        return redirect("/ftp", err="配额与限速必须是整数")

    user = db.get(FtpUser, int(user_id)) if user_id.isdigit() else None
    if user and user.username != username:
        return redirect("/ftp", err="已存在的用户不能修改用户名")
    if user is None:
        user = db.scalar(select(FtpUser).where(FtpUser.username == username))
        if user is not None:
            return redirect("/ftp", err="用户名已存在")

    if user is None:
        if not password:
            return redirect("/ftp", err="新建用户必须设置密码")
        user = FtpUser(
            username=username,
            password=ftp.hash_password(password),
            uid=settings.ftp_uid,
            gid=settings.ftp_gid,
            homedir=f"/srv/ftp/{username}",
        )
        db.add(user)
    elif password:
        user.password = ftp.hash_password(password)

    user.quota_mb = max(quota, 0)
    user.ul_bandwidth = max(ul, 0)
    user.dl_bandwidth = max(dl, 0)
    user.status = 1 if form.get("enabled") is not None else 0
    db.commit()

    cfg = load(db, "ftp", ftp.DEFAULTS)
    result = ftp.apply_config(cfg, [u.username for u in _users(db)])
    audit(db, session["u"], "ftp", f"保存用户 {username}", result.text(), "ok" if result.ok else "fail")
    if not result.ok:
        return redirect("/ftp", err=f"用户已保存，但应用失败：{result.text()}")
    return redirect("/ftp", msg=f"用户 {username} 已保存")


@router.post("/ftp/users/delete")
async def ftp_user_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/ftp", err="CSRF 校验失败，请刷新后重试")
    user = db.get(FtpUser, int(fv(form, "id") or 0))
    if user is None:
        return redirect("/ftp", err="用户不存在")
    username = user.username
    db.delete(user)
    db.commit()
    audit(db, session["u"], "ftp", f"删除用户 {username}", "目录保留在 /srv/ftp 下")
    return redirect("/ftp", msg=f"用户 {username} 已删除（家目录保留）")


@router.post("/ftp/users/test")
async def ftp_user_test(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/ftp", err="CSRF 校验失败，请刷新后重试")
    cfg = load(db, "ftp", ftp.DEFAULTS)
    result = ftp.test_login(fv(form, "username"), fv(form, "password"), use_tls=bool(cfg.get("tls", True)))
    return redirect("/ftp", msg=result if "成功" in result else None, err=None if "成功" in result else result)
