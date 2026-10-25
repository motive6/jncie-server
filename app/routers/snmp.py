import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SnmpDisk, SnmpHost, SnmpIface, SnmpIfHistory, now
from ..security import require_user
from ..services import snmp, snmpmon
from ..services.base import unit_state
from ..services.store import audit, load, save
from ..web import render
from .common import check_csrf, fv, fv_lines, redirect

router = APIRouter()

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
ADDR_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@router.get("/snmp")
async def snmp_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    hosts = list(db.scalars(select(SnmpHost).order_by(SnmpHost.name)))
    edit_id = request.query_params.get("edit", "")
    edit_host = db.get(SnmpHost, int(edit_id)) if edit_id.isdigit() else None
    return render(
        request,
        "snmp.html",
        active="snmp",
        cfg=load(db, "snmp", snmp.DEFAULTS),
        state=unit_state(snmp.UNIT),
        hosts=hosts,
        edit_host=edit_host,
        result=request.query_params.get("result", ""),
    )


@router.post("/snmp/settings")
async def snmp_settings(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")
    community = fv(form, "community")
    if community and not re.match(r"^[A-Za-z0-9_.-]{4,64}$", community):
        return redirect("/snmp", err="团体名需为 4-64 位字母、数字、_ . -")

    traps = []
    for line in fv_lines(form, "traps"):
        host, _, secret = line.partition("=")
        if host.strip() and secret.strip():
            traps.append({"host": host.strip(), "community": secret.strip()})

    cfg = load(db, "snmp", snmp.DEFAULTS)
    cfg.update(
        {
            "enabled": form.get("enabled") is not None,
            "community": community,
            "allowed_networks": fv_lines(form, "allowed_networks"),
            "sys_location": fv(form, "sys_location"),
            "sys_contact": fv(form, "sys_contact"),
            "traps": traps,
        }
    )
    result = snmp.apply_config(cfg)
    if result.ok:
        save(db, "snmp", cfg)
    audit(db, session["u"], "snmp", "保存并应用配置", result.text(), "ok" if result.ok else "fail")
    return redirect("/snmp", msg="SNMP 配置已应用" if result.ok else None, err=None if result.ok else result.text())


@router.post("/snmp/v3/add")
async def snmp_v3_add(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")
    username = fv(form, "username")
    auth_pass = fv(form, "auth_pass")
    priv_pass = fv(form, "priv_pass")
    if not NAME_RE.match(username):
        return redirect("/snmp", err="v3 用户名不合法")
    if not SECRET_RE.match(auth_pass) or not SECRET_RE.match(priv_pass):
        return redirect("/snmp", err="认证/加密口令需为至少 8 位字母、数字、_ . -")
    cfg = load(db, "snmp", snmp.DEFAULTS)
    users = [u for u in (cfg.get("v3_users") or []) if u.get("username") != username]
    users.append({"username": username, "auth_pass": auth_pass, "priv_pass": priv_pass})
    cfg["v3_users"] = users
    result = snmp.apply_config(cfg)
    if result.ok:
        save(db, "snmp", cfg)
    audit(db, session["u"], "snmp", f"保存 v3 用户 {username}", result.text(), "ok" if result.ok else "fail")
    return redirect("/snmp", msg=f"v3 用户 {username} 已保存" if result.ok else None, err=None if result.ok else result.text())


@router.post("/snmp/v3/delete")
async def snmp_v3_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")
    username = fv(form, "username")
    cfg = load(db, "snmp", snmp.DEFAULTS)
    cfg["v3_users"] = [u for u in (cfg.get("v3_users") or []) if u.get("username") != username]
    result = snmp.apply_config(cfg)
    if result.ok:
        save(db, "snmp", cfg)
    audit(db, session["u"], "snmp", f"删除 v3 用户 {username}", result.text(), "ok" if result.ok else "fail")
    return redirect("/snmp", msg=f"v3 用户 {username} 已删除" if result.ok else None, err=None if result.ok else result.text())


@router.post("/snmp/test")
async def snmp_test(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")
    cfg = load(db, "snmp", snmp.DEFAULTS)
    output = snmp.test_get(cfg)
    ok = "STRING:" in output or "OID:" in output
    return redirect("/snmp", msg=output if ok else None, err=None if ok else output)


@router.post("/snmp/hosts")
async def snmp_host_save(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")

    host_id = fv(form, "id")
    name = fv(form, "name")
    address = fv(form, "address")
    version = fv(form, "version") or "2c"
    community = fv(form, "community")
    v3_user = fv(form, "v3_user")
    v3_auth = fv(form, "v3_auth")
    v3_priv = fv(form, "v3_priv")

    host = db.get(SnmpHost, int(host_id)) if host_id.isdigit() else None
    is_new = host is None
    old_auth = host.v3_auth if host else ""
    old_priv = host.v3_priv if host else ""

    if not name or len(name) > 64:
        return redirect("/snmp", err="主机名称必填（不超过 64 字符）")
    if not ADDR_RE.match(address):
        return redirect("/snmp", err="地址格式不合法")
    if version not in ("2c", "3"):
        return redirect("/snmp", err="SNMP 版本只能是 2c 或 3")
    if version == "2c" and not community:
        return redirect("/snmp", err="v2c 需要填写团体名")
    if version == "3":
        if not NAME_RE.match(v3_user):
            return redirect("/snmp", err="v3 用户名不合法")
        if is_new and (not SECRET_RE.match(v3_auth) or not SECRET_RE.match(v3_priv)):
            return redirect("/snmp", err="新建 v3 主机需填写认证与加密口令（至少 8 位）")
        if v3_auth and not SECRET_RE.match(v3_auth):
            return redirect("/snmp", err="认证口令不合法（至少 8 位）")
        if v3_priv and not SECRET_RE.match(v3_priv):
            return redirect("/snmp", err="加密口令不合法（至少 8 位）")

    if host is None:
        host = SnmpHost()
        db.add(host)
    host.name = name
    host.address = address
    host.version = version
    host.community = community
    host.v3_user = v3_user
    host.v3_auth = v3_auth or old_auth
    host.v3_priv = v3_priv or old_priv
    host.enabled = 1 if form.get("enabled") is not None else 0
    db.commit()

    result = snmpmon.poll_host(host)
    snmpmon.apply_poll(db, host, result)
    db.commit()
    audit(
        db, session["u"], "snmp",
        f"保存监控主机 {name}({address})",
        "首次探测成功" if result.get("ok") else f"首次探测失败：{result.get('error', '')}",
        "ok" if result.get("ok") else "fail",
    )
    if result.get("ok"):
        return redirect("/snmp", msg=f"主机 {name} 已保存并探测成功")
    return redirect("/snmp", msg=f"主机 {name} 已保存", err=f"首次探测失败：{result.get('error', '')}")


@router.post("/snmp/hosts/poll")
async def snmp_host_poll(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")
    host = db.get(SnmpHost, int(fv(form, "id") or 0))
    if host is None:
        return redirect("/snmp", err="主机不存在")
    result = snmpmon.poll_host(host)
    snmpmon.apply_poll(db, host, result)
    db.commit()
    if result.get("ok"):
        return redirect("/snmp", msg=f"{host.name} 探测成功")
    return redirect("/snmp", err=f"{host.name} 探测失败：{result.get('error', '')}")


@router.post("/snmp/hosts/delete")
async def snmp_host_delete(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/snmp", err="CSRF 校验失败")
    host = db.get(SnmpHost, int(fv(form, "id") or 0))
    if host is None:
        return redirect("/snmp", err="主机不存在")
    name = host.name
    db.delete(host)
    db.commit()
    audit(db, session["u"], "snmp", f"删除监控主机 {name}")
    return redirect("/snmp", msg=f"主机 {name} 已删除")


@router.get("/snmp/host/{host_id}")
async def snmp_host_detail(request: Request, host_id: int, db: Session = Depends(get_db)):
    require_user(request)
    host = db.get(SnmpHost, host_id)
    if host is None:
        return redirect("/snmp", err="主机不存在")
    disks = list(
        db.scalars(select(SnmpDisk).where(SnmpDisk.host_id == host.id).order_by(SnmpDisk.path))
    )
    ifaces = list(
        db.scalars(select(SnmpIface).where(SnmpIface.host_id == host.id).order_by(SnmpIface.ifindex))
    )
    return render(
        request,
        "snmp_host.html",
        active="snmp",
        active_host=host.id,
        host=host,
        disks=disks,
        ifaces=ifaces,
    )


@router.post("/snmp/host/{host_id}/poll")
async def snmp_host_poll_detail(host_id: int, request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect(f"/snmp/host/{host_id}", err="CSRF 校验失败")
    host = db.get(SnmpHost, host_id)
    if host is None:
        return redirect("/snmp", err="主机不存在")
    result = snmpmon.poll_host(host)
    snmpmon.apply_poll(db, host, result)
    db.commit()
    if result.get("ok"):
        return redirect(f"/snmp/host/{host_id}", msg=f"{host.name} 已刷新")
    return redirect(f"/snmp/host/{host_id}", err=f"探测失败：{result.get('error', '')}")


def _downsample(rows, limit: int = 180):
    if len(rows) <= limit:
        return [[int(row.ts.timestamp()), row.in_bps, row.out_bps] for row in rows]
    step = len(rows) / limit
    points = []
    for index in range(limit):
        start = int(index * step)
        chunk = rows[start:max(int((index + 1) * step), start + 1)]
        if not chunk:
            continue
        points.append(
            [
                int(chunk[-1].ts.timestamp()),
                sum(row.in_bps for row in chunk) // len(chunk),
                sum(row.out_bps for row in chunk) // len(chunk),
            ]
        )
    return points


@router.get("/snmp/host/{host_id}/traffic.json")
async def snmp_host_traffic(host_id: int, request: Request, db: Session = Depends(get_db)):
    require_user(request)
    host = db.get(SnmpHost, host_id)
    if host is None:
        return {"error": "host not found", "points": [], "interfaces": []}

    ifaces = list(
        db.scalars(select(SnmpIface).where(SnmpIface.host_id == host.id).order_by(SnmpIface.ifindex))
    )
    ifindex_param = request.query_params.get("ifindex", "")
    ifindex = int(ifindex_param) if ifindex_param.isdigit() else 0
    if ifindex <= 0 and ifaces:
        ifindex = ifaces[0].ifindex

    minutes_param = request.query_params.get("minutes", "60")
    minutes = int(minutes_param) if minutes_param.isdigit() else 60
    minutes = max(5, min(minutes, 1440))

    rows = list(
        db.scalars(
            select(SnmpIfHistory)
            .where(
                SnmpIfHistory.host_id == host.id,
                SnmpIfHistory.ifindex == ifindex,
                SnmpIfHistory.ts >= now() - timedelta(minutes=minutes),
            )
            .order_by(SnmpIfHistory.ts)
        )
    )
    selected = next((item for item in ifaces if item.ifindex == ifindex), None)
    return {
        "ifindex": ifindex,
        "name": selected.name if selected else "",
        "status": selected.status if selected else 0,
        "speed_mbps": selected.speed_mbps if selected else 0,
        "minutes": minutes,
        "interfaces": [{"ifindex": item.ifindex, "name": item.name} for item in ifaces],
        "points": _downsample(rows),
    }
