import ipaddress
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import require_user
from ..services import network
from ..services.store import audit, load, save
from ..web import render
from .common import check_csrf, fv, redirect

router = APIRouter()

IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


@router.get("/network")
async def network_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    cfg = load(db, "network", network.DEFAULTS)
    interfaces = network.live_interfaces()
    state = network.helper("show")
    pending = network.helper("pending")
    route = network.default_route()

    first = interfaces[0] if interfaces else {}
    current_cidr = (first.get("ipv4") or [""])[0]
    current_gateway = route.split(" ")[0] if route and route != "-" else ""
    managed = bool(state.get("managed"))

    return render(
        request,
        "network.html",
        active="network",
        cfg=cfg,
        state=state,
        pending=pending,
        interfaces=interfaces,
        route=route,
        dns=network.dns_servers(),
        hostname=network.hostname(),
        selected_iface=(cfg.get("interface") if managed else "") or first.get("name", ""),
        selected_mode=(cfg.get("mode") if managed else "") or "dhcp",
        default_cidr=(cfg.get("cidr") if managed else "") or current_cidr,
        default_gateway=(cfg.get("gateway") if managed else "") or current_gateway,
    )


@router.post("/network/apply")
async def network_apply(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/network", err="CSRF 校验失败")

    interface = fv(form, "interface")
    mode = fv(form, "mode") or "dhcp"
    cidr = fv(form, "cidr")
    gateway = fv(form, "gateway")

    if not IFACE_RE.match(interface):
        return redirect("/network", err="网卡名称不合法")
    if interface not in {item["name"] for item in network.live_interfaces()}:
        return redirect("/network", err=f"网卡 {interface} 不存在")
    if mode not in ("dhcp", "static"):
        return redirect("/network", err="模式不合法")
    if mode == "static":
        try:
            iface = ipaddress.ip_interface(cidr)
        except ValueError:
            return redirect("/network", err="地址需要 CIDR 格式，例如 10.10.10.1/24")
        if iface.version != 4:
            return redirect("/network", err="目前只支持 IPv4")
        if gateway:
            try:
                gw = ipaddress.ip_address(gateway)
            except ValueError:
                return redirect("/network", err="网关不是合法 IPv4 地址")
            if gw not in iface.network:
                return redirect("/network", err="网关不在该地址所在网段内")

    spec = {"interface": interface, "mode": mode, "cidr": cidr, "gateway": gateway}
    network.write_spec(spec)
    result = network.helper("apply")
    messages = "\n".join(result.get("messages") or [])
    if result.get("ok"):
        save(db, "network", spec)
    audit(
        db,
        session["u"],
        "network",
        f"应用网络配置 {interface}/{mode}",
        messages,
        "ok" if result.get("ok") else "fail",
    )
    if not result.get("ok"):
        return redirect("/network", err=messages or "应用失败")
    return redirect("/network", msg=messages or "配置已应用")


@router.post("/network/confirm")
async def network_confirm(request: Request, db: Session = Depends(get_db)):
    session = require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/network", err="CSRF 校验失败")
    result = network.helper("confirm")
    messages = "\n".join(result.get("messages") or [])
    audit(db, session["u"], "network", "确认保留网络配置", messages, "ok" if result.get("ok") else "fail")
    if result.get("ok"):
        return redirect("/network", msg=messages or "已保留当前配置")
    return redirect("/network", err=messages or "确认失败")
