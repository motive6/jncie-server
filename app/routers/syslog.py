from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import require_user
from ..services import syslog
from ..services.base import unit_state
from ..services.store import audit, load, save
from ..web import render
from .common import check_csrf, fv, fv_lines, redirect

router = APIRouter()


@router.get("/syslog")
async def syslog_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    cfg = load(db, "syslog", syslog.DEFAULTS)
    page = max(int(request.query_params.get("page", "1") or 1), 1)
    limit = 50
    keyword = request.query_params.get("q", "")
    host = request.query_params.get("host", "")
    program = request.query_params.get("program", "")
    prio = request.query_params.get("prio", "")
    try:
        rows, total = syslog.search(
            keyword=keyword, host=host, program=program, max_priority=prio,
            limit=limit, offset=(page - 1) * limit,
        )
        stats = syslog.stats()
        dberr = ""
    except Exception as exc:
        rows, total, stats = [], 0, {"count": 0, "oldest": None}
        dberr = f"查询日志库失败：{exc}"
    return render(
        request,
        "syslog.html",
        active="syslog",
        cfg=cfg,
        state=unit_state(syslog.UNIT),
        rows=rows,
        total=total,
        page=page,
        pages=max((total + limit - 1) // limit, 1),
        limit=limit,
        q=keyword,
        host=host,
        program=program,
        prio=prio,
        stats=stats,
        dberr=dberr,
        result=request.query_params.get("result", ""),
    )


@router.post("/syslog/settings")
async def syslog_settings(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/syslog", err="CSRF 校验失败")
    cfg = load(db, "syslog", syslog.DEFAULTS)
    try:
        retention = int(fv(form, "retention_days") or 30)
    except ValueError:
        return redirect("/syslog", err="保留天数必须是整数")
    cfg.update(
        {
            "enabled": form.get("enabled") is not None,
            "listen_remote": form.get("listen_remote") is not None,
            "allowed_networks": fv_lines(form, "allowed_networks"),
            "retention_days": max(retention, 1),
        }
    )
    result = syslog.apply_config(cfg)
    if result.ok:
        save(db, "syslog", cfg)
    audit(db, session["u"], "syslog", "保存并应用配置", result.text(), "ok" if result.ok else "fail")
    return redirect("/syslog", msg="Syslog 配置已应用" if result.ok else None, err=None if result.ok else result.text())


@router.post("/syslog/rule/add")
async def syslog_rule_add(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/syslog", err="CSRF 校验失败")
    kind = fv(form, "type")
    value = fv(form, "value")
    action = fv(form, "action")
    target = fv(form, "target")
    if kind not in ("program", "facility", "msg") or action not in ("drop", "forward") or not value:
        return redirect("/syslog", err="规则参数不完整")
    if action == "forward" and not target:
        return redirect("/syslog", err="转发规则需要填写目标地址")
    cfg = load(db, "syslog", syslog.DEFAULTS)
    cfg.setdefault("rules", []).append(
        {"type": kind, "value": value, "action": action, "target": target}
    )
    result = syslog.apply_config(cfg)
    if result.ok:
        save(db, "syslog", cfg)
    audit(db, session["u"], "syslog", f"新增规则 {kind}={value} → {action}", result.text(), "ok" if result.ok else "fail")
    return redirect("/syslog", msg="规则已添加并生效" if result.ok else None, err=None if result.ok else result.text())


@router.post("/syslog/rule/delete")
async def syslog_rule_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/syslog", err="CSRF 校验失败")
    index = int(fv(form, "index") or -1)
    cfg = load(db, "syslog", syslog.DEFAULTS)
    rules = cfg.get("rules") or []
    if 0 <= index < len(rules):
        removed = rules.pop(index)
        result = syslog.apply_config(cfg)
        if result.ok:
            save(db, "syslog", cfg)
        audit(db, session["u"], "syslog", f"删除规则 {removed}", result.text(), "ok" if result.ok else "fail")
        return redirect("/syslog", msg="规则已删除" if result.ok else None, err=None if result.ok else result.text())
    return redirect("/syslog", err="规则不存在")


@router.post("/syslog/prune")
async def syslog_prune(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/syslog", err="CSRF 校验失败")
    cfg = load(db, "syslog", syslog.DEFAULTS)
    days = int(cfg.get("retention_days") or 30)
    removed, message = syslog.prune(days)
    audit(db, session["u"], "syslog", "清理历史日志", message)
    return redirect("/syslog", msg=message)


@router.get("/syslog/tail.json")
async def syslog_tail(request: Request):
    require_user(request)
    rows, _ = syslog.search(limit=20, offset=0)
    return {"rows": rows}
