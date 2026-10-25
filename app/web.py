from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from .config import BASE_DIR, settings
from .db import SessionLocal
from .models import SnmpHost
from .security import read_session

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def _fmt_bps(value) -> str:
    try:
        bits = float(value or 0)
    except (TypeError, ValueError):
        bits = 0.0
    for unit, scale in (("Gbps", 1e9), ("Mbps", 1e6), ("Kbps", 1e3)):
        if bits >= scale:
            return f"{bits / scale:.2f} {unit}"
    return f"{bits:.0f} bps"


def _fmt_size_kb(value) -> str:
    try:
        kb = float(value or 0)
    except (TypeError, ValueError):
        kb = 0.0
    for unit, scale in (("TB", 1024 ** 3), ("GB", 1024 ** 2), ("MB", 1024)):
        if kb >= scale:
            return f"{kb / scale:.1f} {unit}"
    return f"{kb:.0f} KB"


templates.env.filters["bps"] = _fmt_bps
templates.env.filters["size_kb"] = _fmt_size_kb
templates.env.globals.update(
    app_name=settings.app_name, dry_run=settings.dry_run, timezone=settings.timezone
)


def render(request: Request, name: str, **context):
    session = read_session(request)
    context.setdefault("user", session["u"] if session else None)
    context.setdefault("csrf", session["csrf"] if session else "")
    context.setdefault("role", session.get("r", "admin") if session else "viewer")
    context.setdefault("is_admin", context["role"] == "admin")
    context.setdefault("msg", request.query_params.get("msg", ""))
    context.setdefault("err", request.query_params.get("err", ""))
    context.setdefault("active", "")

    if session:
        try:
            with SessionLocal() as db:
                context.setdefault("snmp_hosts", list(db.scalars(select(SnmpHost).order_by(SnmpHost.name))))
        except Exception:
            context.setdefault("snmp_hosts", [])
    else:
        context.setdefault("snmp_hosts", [])
    return templates.TemplateResponse(request=request, name=name, context=context)
