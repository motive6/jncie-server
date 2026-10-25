from pathlib import Path

from ..config import settings
from .base import ApplyResult, FileSpec, apply_service, run_command

UNIT = "dnsmasq"
CONF_PATH = "/etc/dnsmasq.d/svcadmin.conf"
LOG_PATH = Path("/var/log/dnsmasq.log")

DEFAULTS = {
    "enabled": True,
    "listen_addresses": [],
    "upstreams": ["223.5.5.5", "119.29.29.29"],
    "domain_forwarders": [],
    "records": [],
    "cnames": [],
    "blocked": [],
    "cache_size": 1500,
    "local_domain": "",
    "log_queries": True,
}


def render(cfg: dict) -> str:
    lines = [
        "# Managed by SVC Admin. Manual edits will be overwritten.",
        "port=53",
        "no-resolv",
        "no-poll",
        "domain-needed",
        "bogus-priv",
        f"cache-size={int(cfg.get('cache_size') or 1500)}",
    ]

    listens = [x for x in (cfg.get("listen_addresses") or []) if x]
    if listens:
        lines.append("bind-interfaces")
        lines.append("listen-address=" + ",".join(listens))

    local_domain = (cfg.get("local_domain") or "").strip().rstrip(".")
    if local_domain:
        lines.append(f"local=/{local_domain}/")

    for server in cfg.get("upstreams") or []:
        if server:
            lines.append(f"server={server}")

    for rule in cfg.get("domain_forwarders") or []:
        domain = (rule.get("domain") or "").strip()
        servers = [s for s in (rule.get("servers") or []) if s]
        for server in servers:
            lines.append(f"server=/{domain}/{server}")

    for record in cfg.get("records") or []:
        name = (record.get("name") or "").strip()
        value = (record.get("ip") or "").strip()
        if name and value:
            lines.append(f"host-record={name},{value}")

    for cname in cfg.get("cnames") or []:
        name = (cname.get("name") or "").strip()
        target = (cname.get("target") or "").strip()
        if name and target:
            lines.append(f"cname={name},{target}")

    for domain in cfg.get("blocked") or []:
        if domain:
            lines.append(f"address=/{domain}/0.0.0.0")
            lines.append(f"address=/{domain}/::")

    if cfg.get("log_queries"):
        lines.append("log-queries")
        lines.append(f"log-facility={LOG_PATH}")
    return "\n".join(lines) + "\n"


def apply_config(cfg: dict) -> ApplyResult:
    enabled = bool(cfg.get("enabled", True))
    restart = ["systemctl", "restart", UNIT] if enabled else ["systemctl", "stop", UNIT]
    return apply_service(
        "dns",
        files=[FileSpec(CONF_PATH, render(cfg), mode="0644")],
        validate=["dnsmasq", "--test", "-C", "{staged}"] if enabled else [],
        restart=restart,
        health=["systemctl", "is-active", UNIT] if enabled else [],
        health_ports=[53] if enabled else [],
    )


def test_query(name: str, server: str = "127.0.0.1") -> str:
    if settings.dry_run:
        return f"{name}: 192.0.2.1 (dry-run)"
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server]
    resolver.lifetime = 3.0
    results = []
    for rtype in ("A", "AAAA", "CNAME"):
        try:
            for answer in resolver.resolve(name, rtype):
                results.append(f"{rtype} {answer.to_text()}")
        except Exception:
            continue
    return "\n".join(results) if results else "无解析结果"


def recent_log(lines: int = 200) -> list[str]:
    if settings.dry_run:
        return ["dry-run: query log 不可用"]
    try:
        content = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except PermissionError:
        return [f"无权读取 {LOG_PATH}：请确认面板用户属于 adm 组，且文件权限为 0640（属主 dnsmasq）"]
    except OSError as exc:
        return [f"无法读取 {LOG_PATH}: {exc}"]
    return list(reversed(content[-lines:]))
