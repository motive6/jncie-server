from datetime import timedelta
import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, SystemSample, now
from ..security import require_user
from ..services.base import port_open, unit_state
from ..services.sysmon import uptime_seconds
from ..web import render

router = APIRouter()

SERVICES = [
    {"key": "ftp", "label": "FTP", "unit": "pure-ftpd-mysql", "port": 21, "udp": False,
     "url": "/ftp", "desc": "pure-ftpd · 虚拟用户 · TLS"},
    {"key": "dns", "label": "DNS 代理", "unit": "dnsmasq", "port": 53, "udp": False,
     "url": "/dns", "desc": "dnsmasq · 转发 + 缓存 + 本地记录"},
    {"key": "radius", "label": "RADIUS", "unit": "freeradius", "port": 1812, "udp": True,
     "url": "/radius", "desc": "FreeRADIUS · SQL 后端"},
    {"key": "syslog", "label": "Syslog", "unit": "rsyslog", "port": 514, "udp": True,
     "url": "/syslog", "desc": "rsyslog · 入库 MariaDB"},
    {"key": "snmp", "label": "SNMP", "unit": "snmpd", "port": 161, "udp": True,
     "url": "/snmp", "desc": "net-snmp · v2c / v3"},
    {"key": "ntp", "label": "NTP", "unit": "chrony", "port": 123, "udp": True,
     "url": "/ntp", "desc": "chrony · 上游同步 + 内网授时"},
]


@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    statuses = []
    for service in SERVICES:
        state = unit_state(service["unit"])
        statuses.append(
            {
                **service,
                **state,
                "listening": port_open(service["port"], udp=service["udp"]),
            }
        )
    recent = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(6)).all()
    return render(request, "dashboard.html", active="dashboard", statuses=statuses, recent=recent)


@router.get("/dashboard/system.json")
async def system_json(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    minutes_param = request.query_params.get("minutes", "15")
    minutes = int(minutes_param) if minutes_param.isdigit() else 15
    minutes = max(5, min(minutes, 120))
    rows = list(
        db.scalars(
            select(SystemSample)
            .where(SystemSample.ts >= now() - timedelta(minutes=minutes))
            .order_by(SystemSample.ts)
        )
    )
    points = [
        {
            "t": int(row.ts.timestamp()),
            "cpu": row.cpu_pct,
            "mem": row.mem_used_kb,
            "memTotal": row.mem_total_kb,
            "disk": row.disk_used_kb,
            "diskTotal": row.disk_total_kb,
            "load": row.load1,
            "rx": row.net_rx_bps,
            "tx": row.net_tx_bps,
        }
        for row in rows
    ]
    return {
        "minutes": minutes,
        "points": points,
        "latest": points[-1] if points else None,
        "uptime": int(uptime_seconds()),
        "cores": os.cpu_count() or 1,
    }
