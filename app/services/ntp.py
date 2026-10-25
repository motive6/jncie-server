import re

from ..config import settings
from .base import ApplyResult, FileSpec, apply_service, run_command

UNIT = "chrony"
CONF_PATH = "/etc/chrony/chrony.conf"

DEFAULTS = {
    "enabled": True,
    "servers": ["ntp.aliyun.com", "cn.pool.ntp.org"],
    "allow_networks": ["10.10.10.0/24"],
    "local_stratum": 10,
}


def render(cfg: dict) -> str:
    lines = [
        "# Managed by SVC Admin. Manual edits will be overwritten.",
        "driftfile /var/lib/chrony/chrony.drift",
        "rtcsync",
        "makestep 1.0 3",
        "leapsectz right/UTC",
    ]
    for server in cfg.get("servers") or []:
        server = (server or "").strip()
        if server:
            lines.append(f"pool {server} iburst maxsources 4")
    for network in cfg.get("allow_networks") or []:
        network = (network or "").strip()
        if network:
            lines.append(f"allow {network}")
    try:
        stratum = int(cfg.get("local_stratum") or 0)
    except (TypeError, ValueError):
        stratum = 10
    if stratum > 0:
        lines.append(f"local stratum {stratum}")
    return "\n".join(lines) + "\n"


def apply_config(cfg: dict) -> ApplyResult:
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return apply_service("ntp", files=[], restart=["systemctl", "stop", UNIT])
    serves_clients = bool([n for n in (cfg.get("allow_networks") or []) if n.strip()])
    return apply_service(
        "ntp",
        files=[FileSpec(CONF_PATH, render(cfg), mode="0644")],
        validate=["chronyd", "-p", "-f", "{staged}"],
        restart=["systemctl", "restart", UNIT],
        health=["systemctl", "is-active", UNIT],
        health_ports=["123/udp"] if serves_clients else [],
    )


def _parse_tracking(output: str) -> dict:
    info = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip().lower().replace(" ", "_")] = value.strip()
    return info


def tracking() -> dict:
    if settings.dry_run:
        return {
            "reference_id": "771CB7B8 (119.28.183.184)",
            "stratum": "3",
            "system_time": "0.000025 seconds fast of NTP time",
            "last_offset": "-0.014 seconds",
            "leap_status": "Normal",
            "synced": True,
            "local_clock": False,
            "raw": "",
        }
    code, output = run_command(["chronyc", "-n", "tracking"], timeout=10)
    info = _parse_tracking(output)
    reference = str(info.get("reference_id", ""))
    local_clock = reference.startswith("7F7F0101") or "127.127.1.1" in reference
    try:
        stratum = int(str(info.get("stratum", "0")))
    except ValueError:
        stratum = 0
    info["local_clock"] = local_clock
    info["synced"] = (
        not local_clock
        and stratum > 0
        and info.get("leap_status", "").lower() == "normal"
    )
    info["raw"] = output
    return info


def sources() -> list[dict]:
    if settings.dry_run:
        return [
            {"mode": "^*", "address": "119.28.183.184", "stratum": "2", "reach": "377",
             "detail": "+9625us[+9625us] +/- 115ms"},
        ]
    code, output = run_command(["chronyc", "-n", "sources"], timeout=10)
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("MS Name") or line.startswith("="):
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        mode, address, stratum, _poll, reach, _last_rx, rest = parts
        if not re.match(r"^[\^=#*x\-+?]", mode):
            continue
        rows.append(
            {
                "mode": mode,
                "address": address,
                "stratum": stratum,
                "reach": reach,
                "detail": rest,
            }
        )
    return rows
