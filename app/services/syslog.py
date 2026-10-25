from sqlalchemy import create_engine, text

from ..config import settings
from .base import ApplyResult, FileSpec, apply_service

UNIT = "rsyslog"
CONF_PATH = "/etc/rsyslog.d/50-svcadmin.conf"

PRIORITIES = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "err",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}
FACILITIES = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth", 5: "syslog",
    6: "lpr", 7: "news", 8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
    12: "ntp", 13: "security", 14: "console", 15: "solaris-cron", 16: "local0",
    17: "local1", 18: "local2", 19: "local3", 20: "local4", 21: "local5",
    22: "local6", 23: "local7",
}

DEFAULTS = {
    "enabled": True,
    "listen_remote": True,
    "allowed_networks": ["127.0.0.1", "10.10.10.0/24"],
    "rules": [],
    "retention_days": 30,
}

_engine = None


def _logs_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.logs_database_url, pool_pre_ping=True)
    return _engine


def _cond(rule: dict) -> str:
    kind = rule.get("type")
    value = (rule.get("value") or "").replace('"', "")
    if kind == "program":
        return f'$programname == "{value}"'
    if kind == "facility":
        return f'$syslogfacility-text == "{value}"'
    if kind == "msg":
        return f'$msg contains "{value}"'
    return ""


def render(cfg: dict) -> str:
    lines = [
        "# Managed by SVC Admin. Manual edits will be overwritten.",
        'module(load="ommysql")',
    ]
    if cfg.get("listen_remote", True):
        lines.append('module(load="imudp")')
        lines.append('module(load="imtcp")')
        lines.append('ruleset(name="remote") {')
        lines.append("    " + _store_action())
        lines.append("}")
        lines.append('input(type="imudp" port="514" ruleset="remote")')
        lines.append('input(type="imtcp" port="514" ruleset="remote")')
        nets = [n.strip() for n in (cfg.get("allowed_networks") or []) if n.strip()]
        if nets:
            lines.append("$AllowedSender UDP, " + ", ".join(nets))
            lines.append("$AllowedSender TCP, " + ", ".join(nets))

    for rule in cfg.get("rules") or []:
        cond = _cond(rule)
        if not cond:
            continue
        action = rule.get("action")
        if action == "drop":
            lines.append(f"if {cond} then stop")
        elif action == "forward":
            target = (rule.get("target") or "").strip()
            if "@" in target or ":" in target:
                host, _, port = target.partition(":")
                port = port or "514"
                lines.append(
                    f'if {cond} then action(type="omfwd" target="{host}" port="{port}" protocol="udp")'
                )

    lines.append(_store_action())
    return "\n".join(lines) + "\n"


def _store_action() -> str:
    return (
        f'action(type="ommysql" server="localhost" db="logs" '
        f'uid="rsyslog" pwd="{settings.rsyslog_db_password}")'
    )


def apply_config(cfg: dict) -> ApplyResult:
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return apply_service("syslog", files=[], restart=["systemctl", "stop", UNIT])
    return apply_service(
        "syslog",
        files=[FileSpec(CONF_PATH, render(cfg), mode="0644")],
        restart=["systemctl", "restart", UNIT],
        health=["systemctl", "is-active", UNIT],
    )


def search(
    keyword: str = "",
    host: str = "",
    program: str = "",
    max_priority: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if settings.dry_run:
        sample = [
            {
                "ID": 3,
                "ReceivedAt": "2026-10-25 10:00:03",
                "FromHost": "server",
                "FromHostIP": "10.10.10.1",
                "ProgramName": "sshd",
                "Facility": 4,
                "Priority": 6,
                "Message": "dry-run 示例日志：Accepted publickey for root",
            }
        ]
        return sample, 1

    clauses = []
    params: dict = {"limit": limit, "offset": offset}
    if keyword:
        clauses.append("Message LIKE :kw")
        params["kw"] = f"%{keyword}%"
    if host:
        clauses.append("(FromHost LIKE :host OR FromHostIP LIKE :host)")
        params["host"] = f"%{host}%"
    if program:
        clauses.append("ProgramName LIKE :prog")
        params["prog"] = f"%{program}%"
    if max_priority and max_priority.isdigit():
        clauses.append("Priority <= :prio")
        params["prio"] = int(max_priority)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    query = text(
        "SELECT ID, ReceivedAt, FromHost, FromHostIP, ProgramName, Facility, Priority, Message "
        f"FROM SystemEvents {where} ORDER BY ID DESC LIMIT :limit OFFSET :offset"
    )
    count_query = text(f"SELECT COUNT(*) FROM SystemEvents {where}")
    with _logs_engine().connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(query, params)]
        total = conn.execute(count_query, {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar() or 0
    for row in rows:
        row["PriorityName"] = PRIORITIES.get(row.get("Priority"), str(row.get("Priority")))
        row["FacilityName"] = FACILITIES.get(row.get("Facility"), str(row.get("Facility")))
    return rows, total


def stats() -> dict:
    if settings.dry_run:
        return {"count": 1, "oldest": "dry-run"}
    try:
        with _logs_engine().connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM SystemEvents")).scalar() or 0
            oldest = conn.execute(text("SELECT MIN(ReceivedAt) FROM SystemEvents")).scalar()
        return {"count": count, "oldest": oldest}
    except Exception as exc:
        return {"count": 0, "oldest": None, "error": str(exc)}


def prune(days: int) -> tuple[int, str]:
    if settings.dry_run:
        return 0, "dry-run：未删除"
    removed = 0
    with _logs_engine().begin() as conn:
        while True:
            result = conn.execute(
                text("DELETE FROM SystemEvents WHERE ReceivedAt < NOW() - INTERVAL :days DAY LIMIT 5000"),
                {"days": days},
            )
            batch = result.rowcount or 0
            removed += batch
            if batch < 5000:
                break
    return removed, f"已删除 {removed} 条早于 {days} 天的日志"
