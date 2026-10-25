import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AdminUser
from ..security import hash_password, require_admin, require_user
from ..services.store import audit
from ..web import render
from .common import check_csrf, fv, redirect

router = APIRouter()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
ROLES = {"admin": "管理员（可修改）", "viewer": "只读（仅查看）"}


def _admin_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AdminUser).where(AdminUser.role == "admin")) or 0


@router.get("/users")
async def users_page(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    is_admin = session.get("r", "admin") == "admin"
    if is_admin:
        users = list(db.scalars(select(AdminUser).order_by(AdminUser.username)))
    else:
        me = db.scalar(select(AdminUser).where(AdminUser.username == session["u"]))
        users = [me] if me else []
    return render(request, "users.html", active="users", users=users, roles=ROLES)


@router.post("/users/add")
async def user_add(request: Request, db: Session = Depends(get_db)):
    session = require_admin(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/users", err="CSRF 校验失败")
    username = fv(form, "username")
    password = fv(form, "password")
    role = fv(form, "role") or "viewer"
    if not USERNAME_RE.match(username):
        return redirect("/users", err="用户名需为 2-32 位字母、数字、_ . -")
    if len(password) < 8:
        return redirect("/users", err="密码至少 8 位")
    if role not in ROLES:
        return redirect("/users", err="角色不合法")
    if db.scalar(select(AdminUser).where(AdminUser.username == username)):
        return redirect("/users", err="用户名已存在")
    db.add(AdminUser(username=username, password_hash=hash_password(password), role=role))
    db.commit()
    audit(db, session["u"], "users", f"创建账号 {username}（{role}）")
    return redirect("/users", msg=f"账号 {username} 已创建")


@router.post("/users/password")
async def user_password(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/users", err="CSRF 校验失败")
    username = fv(form, "username") or session["u"]
    if session.get("r", "admin") != "admin" and username != session["u"]:
        return redirect("/users", err="只能修改自己的密码")
    password = fv(form, "password")
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None:
        return redirect("/users", err="账号不存在")
    if len(password) < 8:
        return redirect("/users", err="密码至少 8 位")
    user.password_hash = hash_password(password)
    db.commit()
    audit(db, session["u"], "users", f"重置 {username} 的密码")
    return redirect("/users", msg=f"{username} 的密码已更新")


@router.post("/users/role")
async def user_role(request: Request, db: Session = Depends(get_db)):
    session = require_admin(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/users", err="CSRF 校验失败")
    username = fv(form, "username")
    role = fv(form, "role")
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or role not in ROLES:
        return redirect("/users", err="参数不合法")
    if user.role == "admin" and role != "admin" and _admin_count(db) <= 1:
        return redirect("/users", err="至少要保留一个管理员")
    user.role = role
    db.commit()
    audit(db, session["u"], "users", f"修改 {username} 的角色为 {role}")
    return redirect("/users", msg=f"{username} 的角色已更新（重新登录后生效）")


@router.post("/users/delete")
async def user_delete(request: Request, db: Session = Depends(get_db)):
    session = require_admin(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/users", err="CSRF 校验失败")
    username = fv(form, "username")
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None:
        return redirect("/users", err="账号不存在")
    if username == session["u"]:
        return redirect("/users", err="不能删除当前登录的账号")
    if user.role == "admin" and _admin_count(db) <= 1:
        return redirect("/users", err="至少要保留一个管理员")
    db.delete(user)
    db.commit()
    audit(db, session["u"], "users", f"删除账号 {username}")
    return redirect("/users", msg=f"账号 {username} 已删除")


@router.get("/account")
async def account_page(request: Request):
    require_user(request)
    return redirect("/users")
