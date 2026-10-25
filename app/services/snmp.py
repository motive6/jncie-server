import re

from ..config import settings
from .base import ApplyResult, FileSpec, apply_service, run_command

UNIT = "snmpd"
CONF_PATH = "/etc/snmp/snmpd.conf"

DEFAULTS = {
    "enabled": True,
    "community": "public",
    "allowed_networks": ["127.0.0.1", "10.10.10.0/24"],
    "sys_location": "Server Room",
    "sys_contact": "admin@localhost",
    "v3_users": [],
    "traps": [],
}

_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_COMMUNITY_RE = re.compile(r"^[A-Za-z0-9_.-]{4,64}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")


def _q(value: str) -> str:
    return '"' + str(value).replace('"', "") + '"'


def _plain(value: str) -> str:
    return str(value).replace('"', "").replace("\n", " ").strip()


def render(cfg: dict) -> str:
    lines = [
        "# Managed by SVC Admin. Manual edits will be overwritten.",
        "agentaddress udp:161",
        f"sysLocation {_plain(cfg.get('sys_location') or 'Server Room')}",
        f"sysContact {_plain(cfg.get('sys_contact') or 'admin@localhost')}",
        "sysServices 72",
        "dontLogTCPWrappersConnects yes",
    ]
    community = (cfg.get("community") or "public").strip()
    if community and _COMMUNITY_RE.match(community):
        for net in cfg.get("allowed_networks") or ["127.0.0.1"]:
            lines.append(f"rocommunity {community} {net.strip()}")

    for user in cfg.get("v3_users") or []:
        name = (user.get("username") or "").strip()
        auth_pass = (user.get("auth_pass") or "").strip()
        priv_pass = (user.get("priv_pass") or "").strip()
        if not (_USER_RE.match(name) and _SECRET_RE.match(auth_pass) and _SECRET_RE.match(priv_pass)):
            continue
        lines.append(f"createUser {name} SHA {_q(auth_pass)} AES {_q(priv_pass)}")
        lines.append(f"rouser {name} authpriv")

    for sink in cfg.get("traps") or []:
        host = (sink.get("host") or "").strip()
        secret = (sink.get("community") or "").strip()
        if host and secret:
            lines.append(f"trap2sink {host} {secret}")
    return "\n".join(lines) + "\n"


def apply_config(cfg: dict) -> ApplyResult:
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return apply_service("snmp", files=[], restart=["systemctl", "stop", UNIT])
    return apply_service(
        "snmp",
        files=[FileSpec(CONF_PATH, render(cfg), mode="0644")],
        restart=["systemctl", "restart", UNIT],
        health=["systemctl", "is-active", UNIT],
    )


def test_get(cfg: dict) -> str:
    if settings.dry_run:
        return "dry-run: SNMP sysDescr 不可用"
    community = (cfg.get("community") or "public").strip()
    code, output = run_command(
        ["snmpget", "-v2c", "-c", community, "-t", "2", "-r", "1", "127.0.0.1", "1.3.6.1.2.1.1.1.0"],
        timeout=10,
    )
    return output or f"无响应（exit {code}）"
