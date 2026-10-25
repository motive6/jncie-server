import json
import socket
from pathlib import Path

from ..config import settings
from .base import run_command

HELPER = "/usr/local/sbin/svcadmin-net"

DEFAULTS = {
    "interface": "",
    "mode": "dhcp",
    "cidr": "",
    "gateway": "",
}


def _parse_json(output: str) -> dict | None:
    for line in reversed((output or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def helper(subcommand: str, *args: str) -> dict:
    if settings.dry_run:
        return {
            "ok": True,
            "messages": ["dry-run：未真正修改网络"],
            "pending": False,
            "managed": True,
            "mode": "dhcp",
        }
    code, output = run_command(["sudo", "-n", HELPER, subcommand, *args], timeout=180)
    payload = _parse_json(output)
    if payload is None:
        return {"ok": False, "messages": [f"网络助手返回异常（exit {code}）：{output[-500:]}"]}
    return payload


def write_spec(spec: dict) -> None:
    stage = settings.staging_dir / "network"
    if settings.dry_run:
        return
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")


def live_interfaces() -> list[dict]:
    if settings.dry_run:
        return [
            {"name": "ens32", "state": "up", "mac": "00:0c:29:aa:bb:cc",
             "ipv4": ["10.10.10.1/24"], "ipv6": []},
        ]
    code, output = run_command(["ip", "-j", "addr", "show"], timeout=10)
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    interfaces = []
    for item in data if isinstance(data, list) else []:
        name = item.get("ifname")
        if not name or name == "lo":
            continue
        ipv4 = [
            f"{addr.get('local')}/{addr.get('prefixlen')}"
            for addr in item.get("addr_info", [])
            if addr.get("family") == "inet"
        ]
        ipv6 = [
            f"{addr.get('local')}/{addr.get('prefixlen')}"
            for addr in item.get("addr_info", [])
            if addr.get("family") == "inet6" and addr.get("scope") == "global"
        ]
        interfaces.append(
            {
                "name": name,
                "state": item.get("operstate", ""),
                "mac": item.get("address", ""),
                "ipv4": ipv4,
                "ipv6": ipv6,
            }
        )
    return interfaces


def default_route() -> str:
    if settings.dry_run:
        return "10.10.10.254 dev ens32"
    code, output = run_command(["ip", "-j", "route", "show", "default"], timeout=10)
    try:
        routes = json.loads(output)
    except json.JSONDecodeError:
        return "-"
    parts = [f"{item.get('gateway')} dev {item.get('dev')}" for item in routes if item.get("gateway")]
    return ", ".join(parts) or "-"


def dns_servers() -> list[str]:
    if settings.dry_run:
        return ["127.0.0.1", "10.10.10.254"]
    try:
        lines = Path("/etc/resolv.conf").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.split()[1] for line in lines if line.strip().startswith("nameserver") and len(line.split()) > 1]


def hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "-"
