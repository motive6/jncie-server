from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .config import settings
from .db import Base

TZ = ZoneInfo(settings.timezone)


def now() -> datetime:
    """返回配置时区下的本地时间（数据库中的 DATETIME 统一存本地时间）。"""
    return datetime.now(TZ).replace(tzinfo=None)


utcnow = now  # 兼容旧引用


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SnmpHost(Base):
    __tablename__ = "snmp_host"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(8), default="2c")
    community: Mapped[str] = mapped_column(String(128), default="")
    v3_user: Mapped[str] = mapped_column(String(64), default="")
    v3_auth: Mapped[str] = mapped_column(String(128), default="")
    v3_priv: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    sys_name: Mapped[str] = mapped_column(String(128), default="")
    sys_descr: Mapped[str] = mapped_column(Text, default="")
    sys_location: Mapped[str] = mapped_column(String(128), default="")
    sys_uptime: Mapped[str] = mapped_column(String(64), default="")
    mem_total_kb: Mapped[int] = mapped_column(Integer, default=0)
    mem_avail_kb: Mapped[int] = mapped_column(Integer, default=0)
    swap_total_kb: Mapped[int] = mapped_column(Integer, default=0)
    swap_avail_kb: Mapped[int] = mapped_column(Integer, default=0)
    last_ok: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(255), default="")
    last_polled: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SnmpDisk(Base):
    __tablename__ = "snmp_disk"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(Integer, index=True)
    path: Mapped[str] = mapped_column(String(255), default="")
    total_kb: Mapped[int] = mapped_column(Integer, default=0)
    avail_kb: Mapped[int] = mapped_column(Integer, default=0)
    used_kb: Mapped[int] = mapped_column(Integer, default=0)


class SnmpIface(Base):
    __tablename__ = "snmp_iface"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(Integer, index=True)
    ifindex: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    speed_mbps: Mapped[int] = mapped_column(Integer, default=0)
    in_octets: Mapped[int] = mapped_column(Integer, default=0)
    out_octets: Mapped[int] = mapped_column(Integer, default=0)
    in_bps: Mapped[int] = mapped_column(Integer, default=0)
    out_bps: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SnmpIfHistory(Base):
    __tablename__ = "snmp_ifhistory"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(Integer, index=True)
    ifindex: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    in_bps: Mapped[int] = mapped_column(Integer, default=0)
    out_bps: Mapped[int] = mapped_column(Integer, default=0)


class SystemSample(Base):
    __tablename__ = "system_sample"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mem_used_kb: Mapped[int] = mapped_column(Integer, default=0)
    mem_total_kb: Mapped[int] = mapped_column(Integer, default=0)
    swap_used_kb: Mapped[int] = mapped_column(Integer, default=0)
    swap_total_kb: Mapped[int] = mapped_column(Integer, default=0)
    disk_used_kb: Mapped[int] = mapped_column(Integer, default=0)
    disk_total_kb: Mapped[int] = mapped_column(Integer, default=0)
    load1: Mapped[float] = mapped_column(Float, default=0.0)
    net_rx_bps: Mapped[int] = mapped_column(Integer, default=0)
    net_tx_bps: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64))
    service: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(16), default="ok")


class ServiceConfig(Base):
    __tablename__ = "service_config"

    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FtpUser(Base):
    __tablename__ = "ftp_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password: Mapped[str] = mapped_column(String(255))
    uid: Mapped[int] = mapped_column(Integer, default=2001)
    gid: Mapped[int] = mapped_column(Integer, default=2001)
    homedir: Mapped[str] = mapped_column(String(255), default="/srv/ftp")
    quota_mb: Mapped[int] = mapped_column(Integer, default=0)
    ul_bandwidth: Mapped[int] = mapped_column(Integer, default=0)
    dl_bandwidth: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Backup(Base):
    __tablename__ = "backup"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    service: Mapped[str] = mapped_column(String(32), index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    files: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[int] = mapped_column(Integer, default=1)
