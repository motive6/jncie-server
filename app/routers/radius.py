import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import require_user
from ..services import radius
from ..services.base import unit_state
from ..services.store import audit, load
from ..web import render
from .common import check_csrf, fv, redirect

router = APIRouter()

NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


@router.get("/radius")
async def radius_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    tab = request.query_params.get("tab", "users")
    edit_user = request.query_params.get("user", "")
    edit_group = request.query_params.get("group", "")
    edit_nas = request.query_params.get("nas", "")
    error = ""
    try:
        nas_list = radius.list_nas()
        user_list = radius.list_users()
        group_list = radius.list_groups()
        sessions = radius.list_sessions(active_only=True, limit=30)
        user_form = radius.get_user(edit_user) if edit_user else None
        group_form = radius.get_group(edit_group) if edit_group else None
        nas_form = next((n for n in nas_list if str(n.get("id")) == edit_nas), None)
    except Exception as exc:
        error = f"读取 RADIUS 数据库失败：{exc}"
        nas_list, user_list, group_list, sessions = [], [], [], []
        user_form = group_form = nas_form = None
    return render(
        request,
        "radius.html",
        active="radius",
        tab=tab,
        cfg=load(db, "radius", radius.DEFAULTS),
        state=unit_state(radius.UNIT),
        nas_list=nas_list,
        user_list=user_list,
        group_list=group_list,
        sessions=sessions,
        user_form=user_form,
        group_form=group_form,
        nas_form=nas_form,
        edit_user=edit_user,
        edit_group=edit_group,
        dberr=error,
        result=request.query_params.get("result", ""),
    )


@router.post("/radius/nas/save")
async def nas_save(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=nas", err="CSRF 校验失败")
    nasname = fv(form, "nasname")
    shortname = fv(form, "shortname") or nasname
    secret = fv(form, "secret")
    if not nasname or not secret:
        return redirect("/radius?tab=nas", err="地址和共享密钥必填")
    try:
        radius.save_nas(
            {
                "id": int(fv(form, "id") or 0) or None,
                "nasname": nasname,
                "shortname": shortname,
                "type": fv(form, "type") or "other",
                "secret": secret,
                "description": fv(form, "description") or "RADIUS Client",
            }
        )
    except Exception as exc:
        return redirect("/radius?tab=nas", err=f"保存失败：{exc}")
    result = radius.apply_config(db)
    audit(db, session["u"], "radius", f"保存 NAS {nasname}", result.text(), "ok" if result.ok else "fail")
    return redirect("/radius?tab=nas", msg="NAS 已保存，FreeRADIUS 已重载" if result.ok else None, err=None if result.ok else result.text())


@router.post("/radius/nas/delete")
async def nas_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=nas", err="CSRF 校验失败")
    try:
        radius.delete_nas(int(fv(form, "id") or 0))
    except Exception as exc:
        return redirect("/radius?tab=nas", err=f"删除失败：{exc}")
    result = radius.apply_config(db)
    audit(db, session["u"], "radius", "删除 NAS", result.text(), "ok" if result.ok else "fail")
    return redirect("/radius?tab=nas", msg="NAS 已删除")


@router.post("/radius/user/save")
async def user_save(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=users", err="CSRF 校验失败")
    username = fv(form, "username")
    if not NAME_RE.match(username):
        return redirect("/radius?tab=users", err="用户名只能包含字母、数字、_.@-")
    try:
        radius.save_user(
            username,
            fv(form, "password"),
            fv(form, "groups"),
            fv(form, "check_attrs"),
            fv(form, "reply_attrs"),
        )
    except Exception as exc:
        return redirect("/radius?tab=users", err=f"保存失败：{exc}")
    audit(db, session["u"], "radius", f"保存用户 {username}", "数据库已更新")
    return redirect("/radius?tab=users", msg=f"用户 {username} 已保存")


@router.post("/radius/user/delete")
async def user_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=users", err="CSRF 校验失败")
    username = fv(form, "username")
    try:
        radius.delete_user(username)
    except Exception as exc:
        return redirect("/radius?tab=users", err=f"删除失败：{exc}")
    audit(db, session["u"], "radius", f"删除用户 {username}")
    return redirect("/radius?tab=users", msg=f"用户 {username} 已删除")


@router.post("/radius/group/save")
async def group_save(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=groups", err="CSRF 校验失败")
    name = fv(form, "groupname")
    if not NAME_RE.match(name):
        return redirect("/radius?tab=groups", err="组名只能包含字母、数字、_.@-")
    try:
        radius.save_group(name, fv(form, "check_attrs"), fv(form, "reply_attrs"))
    except Exception as exc:
        return redirect("/radius?tab=groups", err=f"保存失败：{exc}")
    audit(db, session["u"], "radius", f"保存用户组 {name}")
    return redirect("/radius?tab=groups", msg=f"组 {name} 已保存")


@router.post("/radius/group/delete")
async def group_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=groups", err="CSRF 校验失败")
    name = fv(form, "groupname")
    try:
        radius.delete_group(name)
    except Exception as exc:
        return redirect("/radius?tab=groups", err=f"删除失败：{exc}")
    audit(db, session["u"], "radius", f"删除用户组 {name}")
    return redirect("/radius?tab=groups", msg=f"组 {name} 已删除")


@router.post("/radius/apply")
async def radius_apply(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius", err="CSRF 校验失败")
    result = radius.apply_config(db)
    audit(db, session["u"], "radius", "重载服务", result.text(), "ok" if result.ok else "fail")
    return redirect("/radius", msg=result.text() if result.ok else None, err=None if result.ok else result.text())


@router.post("/radius/test")
async def radius_test(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/radius?tab=users", err="CSRF 校验失败")
    username = fv(form, "username")
    password = fv(form, "password")
    secret, created = radius.ensure_test_client(db)
    if created:
        radius.apply_config(db)
    result = radius.test_auth(username, password, secret)
    ok = "成功" in result
    return redirect("/radius?tab=users", msg=result if ok else None, err=None if ok else result)
