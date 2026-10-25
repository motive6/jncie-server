from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import now
from ..security import require_user
from ..services import ntp
from ..services.base import unit_state
from ..services.store import audit, load, save
from ..util import is_domain, is_ip, is_network
from ..web import render
from .common import check_csrf, fv, fv_lines, redirect

router = APIRouter()


@router.get("/ntp")
async def ntp_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    cfg = load(db, "ntp", ntp.DEFAULTS)
    return render(
        request,
        "ntp.html",
        active="ntp",
        cfg=cfg,
        state=unit_state(ntp.UNIT),
        tracking=ntp.tracking(),
        sources=ntp.sources(),
        panel_time=now().strftime("%Y-%m-%d %H:%M:%S"),
    )


@router.post("/ntp/settings")
async def ntp_settings(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/ntp", err="CSRF 校验失败")

    servers = fv_lines(form, "servers")
    for server in servers:
        if not (is_domain(server) or is_ip(server)):
            return redirect("/ntp", err=f"上游地址不合法：{server}")

    networks = fv_lines(form, "allow_networks")
    for network in networks:
        if not is_network(network):
            return redirect("/ntp", err=f"允许网段不合法：{network}")

    try:
        stratum = int(fv(form, "local_stratum") or 10)
    except ValueError:
        return redirect("/ntp", err="本地层级必须是整数")
    if not 0 <= stratum <= 15:
        return redirect("/ntp", err="本地层级需在 0-15 之间（0 表示不提供本地参考）")

    cfg = {
        "enabled": form.get("enabled") is not None,
        "servers": servers,
        "allow_networks": networks,
        "local_stratum": stratum,
    }
    result = ntp.apply_config(cfg)
    if result.ok:
        save(db, "ntp", cfg)
    audit(db, session["u"], "ntp", "保存并应用 NTP 配置", result.text(), "ok" if result.ok else "fail")
    if not result.ok:
        return redirect("/ntp", err=result.text())
    return redirect("/ntp", msg="NTP 配置已应用")
