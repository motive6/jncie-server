from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import require_user
from ..services import dns
from ..services.base import unit_state
from ..services.store import audit, load, save
from ..util import is_domain, is_ip, split_lines
from ..web import render
from .common import check_csrf, fv, fv_lines, redirect

router = APIRouter()


def _current(db: Session) -> dict:
    return load(db, "dns", dns.DEFAULTS)


@router.get("/dns")
async def dns_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    cfg = _current(db)
    query = request.query_params.get("q", "").strip()
    result = dns.test_query(query) if query else ""
    log_lines = dns.recent_log() if request.query_params.get("log") else []
    return render(
        request,
        "dns.html",
        active="dns",
        cfg=cfg,
        state=unit_state(dns.UNIT),
        query=query,
        result=result,
        log_lines=log_lines,
    )


@router.post("/dns/settings")
async def dns_settings(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    form = await request.form()
    if not check_csrf(request, form):
        return redirect("/dns", err="CSRF 校验失败，请刷新后重试")

    upstreams = fv_lines(form, "upstreams")
    records = []
    cnames = []
    forwarders = []
    errors = []

    for line in fv_lines(form, "records"):
        name, _, ip = line.partition("=")
        if not (name and is_domain(name) and is_ip(ip)):
            errors.append(f"本地记录格式错误：{line}")
        else:
            records.append({"name": name.strip(), "ip": ip.strip()})

    for line in fv_lines(form, "cnames"):
        name, _, target = line.partition("=")
        if not (name and is_domain(name) and is_domain(target)):
            errors.append(f"CNAME 格式错误：{line}")
        else:
            cnames.append({"name": name.strip(), "target": target.strip().rstrip(".")})

    for line in fv_lines(form, "domain_forwarders"):
        domain, _, servers = line.partition("=")
        server_list = [s.strip() for s in servers.split(",") if s.strip()]
        if not (domain and is_domain(domain) and server_list and all(is_ip(s) for s in server_list)):
            errors.append(f"域名转发格式错误：{line}")
        else:
            forwarders.append({"domain": domain.strip(), "servers": server_list})

    for server in upstreams:
        if not is_ip(server):
            errors.append(f"上游服务器不是合法 IP：{server}")

    blocked = [d for d in fv_lines(form, "blocked") if is_domain(d)]
    if any(not is_domain(d) for d in fv_lines(form, "blocked")):
        errors.append("屏蔽域名列表中存在非法域名")

    if errors:
        return redirect("/dns", err="；".join(errors[:4]))

    try:
        cache_size = int(fv(form, "cache_size") or 1500)
    except ValueError:
        return redirect("/dns", err="缓存条数必须是整数")

    cfg = {
        "enabled": form.get("enabled") is not None,
        "listen_addresses": fv_lines(form, "listen_addresses"),
        "upstreams": upstreams,
        "domain_forwarders": forwarders,
        "records": records,
        "cnames": cnames,
        "blocked": blocked,
        "cache_size": cache_size,
        "local_domain": fv(form, "local_domain").rstrip("."),
        "log_queries": form.get("log_queries") is not None,
    }
    result = dns.apply_config(cfg)
    if result.ok:
        save(db, "dns", cfg)
    audit(db, require_user(request)["u"], "dns", "保存并应用配置", result.text(), "ok" if result.ok else "fail")
    if not result.ok:
        return redirect("/dns", err=result.text())
    return redirect("/dns", msg="DNS 配置已应用")
