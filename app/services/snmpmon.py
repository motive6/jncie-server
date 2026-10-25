import re
from datetime import timedelta

from sqlalchemy import delete, select

from ..config import settings
from ..models import SnmpDisk, SnmpHost, SnmpIface, SnmpIfHistory, now
from .base import run_command

SYS_OIDS = {
    "sys_descr": "1.3.6.1.2.1.1.1.0",
    "sys_uptime": "1.3.6.1.2.1.1.3.0",
    "sys_name": "1.3.6.1.2.1.1.5.0",
    "sys_location": "1.3.6.1.2.1.1.6.0",
}
UCD_MEM_OIDS = {
    "mem_total_kb": "1.3.6.1.4.1.2021.4.5.0",
    "mem_avail_kb": "1.3.6.1.4.1.2021.4.6.0",
    "swap_total_kb": "1.3.6.1.4.1.2021.4.3.0",
    "swap_avail_kb": "1.3.6.1.4.1.2021.4.4.0",
}
IF_TABLE = "1.3.6.1.2.1.2.2.1"
DSK_TABLE = "1.3.6.1.4.1.2021.9.1"
HR_STORAGE = "1.3.6.1.2.1.25.2.3.1"
HR_RAM_SUFFIX = "1.3.6.1.2.1.25.2.1.2"
HR_FIXED_SUFFIX = "1.3.6.1.2.1.25.2.1.4"
MAX_IFACES = 64


def _base_args(host: SnmpHost) -> list[str]:
    if host.version == "3":
        return [
            "snmpget", "-v3", "-l", "authPriv", "-u", host.v3_user,
            "-a", "SHA", "-A", host.v3_auth, "-x", "AES", "-X", host.v3_priv,
            "-t", "2", "-r", "1", "-On",
        ]
    return ["snmpget", "-v2c", "-c", host.community, "-t", "2", "-r", "1", "-On"]


def _walk_args(host: SnmpHost) -> list[str]:
    if host.version == "3":
        return [
            "snmpwalk", "-v3", "-l", "authPriv", "-u", host.v3_user,
            "-a", "SHA", "-A", host.v3_auth, "-x", "AES", "-X", host.v3_priv,
            "-t", "3", "-r", "1", "-On",
        ]
    return ["snmpwalk", "-v2c", "-c", host.community, "-t", "3", "-r", "1", "-On"]


def _parse_value(raw: str):
    raw = raw.strip()
    if raw.startswith("STRING:"):
        text = raw.split(":", 1)[1].strip()
        return text.replace('\\"', '"').replace("\\\\", "\\").strip('"')
    if raw.startswith("OID:"):
        return raw[4:].strip().lstrip(".")
    if raw.startswith("Timeticks:"):
        match = re.search(r"\((\d+)\)", raw)
        return int(match.group(1)) if match else 0
    if ":" in raw:
        token = raw.split(":", 1)[1].strip()
        try:
            return int(token)
        except ValueError:
            return token
    return raw


def _format_ticks(total: int) -> str:
    days, rem = divmod(int(total), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _get_map(host: SnmpHost, oids: dict[str, str]) -> dict[str, object]:
    args = _base_args(host) + [host.address] + list(oids.values())
    code, output = run_command(args, timeout=15)
    values: dict[str, object] = {}
    for line in output.splitlines():
        if " = " not in line:
            continue
        oid, _, raw = line.partition(" = ")
        oid = oid.strip().lstrip(".")
        for key, target in oids.items():
            if oid == target:
                values[key] = _parse_value(raw)
    return values


def _walk(host: SnmpHost, base: str) -> dict[str, dict[str, object]]:
    args = _walk_args(host) + [host.address, base]
    code, output = run_command(args, timeout=25)
    depth = len(base.split("."))
    rows: dict[str, dict[str, object]] = {}
    for line in output.splitlines():
        if " = " not in line:
            continue
        oid, _, raw = line.partition(" = ")
        parts = oid.strip().lstrip(".").split(".")
        if len(parts) < depth + 2:
            continue
        column = parts[depth]
        index = ".".join(parts[depth + 1:])
        rows.setdefault(index, {})[column] = _parse_value(raw)
    return rows


def _collect_memory(host: SnmpHost) -> dict[str, int]:
    values = _get_map(host, UCD_MEM_OIDS)
    memory = {key: int(values.get(key) or 0) for key in UCD_MEM_OIDS}
    if memory["mem_total_kb"] > 0:
        return memory

    for entry in _walk(host, HR_STORAGE).values():
        if str(entry.get("2", "")).endswith(HR_RAM_SUFFIX):
            units = int(entry.get("4") or 1)
            size = int(entry.get("5") or 0)
            used = int(entry.get("6") or 0)
            total_kb = units * size // 1024
            used_kb = units * used // 1024
            return {
                "mem_total_kb": total_kb,
                "mem_avail_kb": max(total_kb - used_kb, 0),
                "swap_total_kb": 0,
                "swap_avail_kb": 0,
            }
    return memory


def _collect_disks(host: SnmpHost) -> list[dict]:
    disks = []
    for entry in _walk(host, DSK_TABLE).values():
        path = str(entry.get("2") or "")
        total = int(entry.get("6") or 0)
        if not path or total <= 0:
            continue
        avail = int(entry.get("7") or 0)
        disks.append({"path": path, "total_kb": total, "avail_kb": avail, "used_kb": int(entry.get("8") or 0)})
    if disks:
        return sorted(disks, key=lambda d: d["path"])[:32]

    for entry in _walk(host, HR_STORAGE).values():
        if not str(entry.get("2", "")).endswith(HR_FIXED_SUFFIX):
            continue
        units = int(entry.get("4") or 1)
        size = int(entry.get("5") or 0)
        used = int(entry.get("6") or 0)
        total_kb = units * size // 1024
        used_kb = units * used // 1024
        if total_kb <= 0:
            continue
        disks.append(
            {
                "path": str(entry.get("3") or "disk"),
                "total_kb": total_kb,
                "avail_kb": max(total_kb - used_kb, 0),
                "used_kb": used_kb,
            }
        )
    return sorted(disks, key=lambda d: d["path"])[:32]


def _collect_interfaces(host: SnmpHost) -> list[dict]:
    interfaces = []
    for index, entry in _walk(host, IF_TABLE).items():
        name = str(entry.get("2") or "")
        if not name:
            continue
        interfaces.append(
            {
                "ifindex": int(index) if index.isdigit() else 0,
                "name": name,
                "status": int(entry.get("8") or 0),
                "speed_mbps": int(int(entry.get("5") or 0) / 1_000_000),
                "in_octets": int(entry.get("10") or 0),
                "out_octets": int(entry.get("16") or 0),
            }
        )
    return sorted(interfaces, key=lambda i: i["ifindex"])[:MAX_IFACES]


def poll_host(host: SnmpHost) -> dict:
    if settings.dry_run:
        return {
            "ok": True,
            "sys_descr": "dry-run 设备",
            "sys_name": host.name,
            "sys_location": "Lab",
            "sys_uptime": "1天 00:00:00",
            "memory": {"mem_total_kb": 8_000_000, "mem_avail_kb": 5_000_000,
                       "swap_total_kb": 2_000_000, "swap_avail_kb": 1_900_000},
            "disks": [{"path": "/", "total_kb": 20_000_000, "avail_kb": 12_000_000, "used_kb": 8_000_000}],
            "interfaces": [{"ifindex": 1, "name": "eth0", "status": 1, "speed_mbps": 1000,
                            "in_octets": 100000, "out_octets": 50000}],
        }

    sys_values = _get_map(host, SYS_OIDS)
    if not sys_values:
        return {"ok": False, "error": "无响应（超时或不可达）"}

    memory = _collect_memory(host)
    disks = _collect_disks(host)
    interfaces = _collect_interfaces(host)

    return {
        "ok": True,
        "sys_descr": str(sys_values.get("sys_descr", "")),
        "sys_name": str(sys_values.get("sys_name", "")),
        "sys_location": str(sys_values.get("sys_location", "")),
        "sys_uptime": _format_ticks(int(sys_values.get("sys_uptime") or 0)),
        "memory": memory,
        "disks": disks,
        "interfaces": interfaces,
    }


def apply_poll(db, host: SnmpHost, result: dict) -> None:
    timestamp = now()
    host.last_polled = timestamp
    host.last_ok = 1 if result.get("ok") else 0
    host.last_error = "" if result.get("ok") else str(result.get("error", ""))[:200]
    if not result.get("ok"):
        return

    host.sys_name = result.get("sys_name") or host.sys_name
    host.sys_descr = (result.get("sys_descr") or "")[:1000]
    host.sys_location = result.get("sys_location") or ""
    host.sys_uptime = result.get("sys_uptime") or ""

    memory = result.get("memory") or {}
    host.mem_total_kb = int(memory.get("mem_total_kb") or 0)
    host.mem_avail_kb = int(memory.get("mem_avail_kb") or 0)
    host.swap_total_kb = int(memory.get("swap_total_kb") or 0)
    host.swap_avail_kb = int(memory.get("swap_avail_kb") or 0)

    db.execute(delete(SnmpDisk).where(SnmpDisk.host_id == host.id))
    for disk in result.get("disks") or []:
        db.add(
            SnmpDisk(
                host_id=host.id,
                path=str(disk.get("path", ""))[:255],
                total_kb=int(disk.get("total_kb") or 0),
                avail_kb=int(disk.get("avail_kb") or 0),
                used_kb=int(disk.get("used_kb") or 0),
            )
        )

    previous = {
        row.ifindex: row
        for row in db.scalars(select(SnmpIface).where(SnmpIface.host_id == host.id)).all()
    }
    db.execute(delete(SnmpIface).where(SnmpIface.host_id == host.id))
    for iface in result.get("interfaces") or []:
        ifindex = int(iface.get("ifindex") or 0)
        old = previous.get(ifindex)
        in_bps = out_bps = 0
        if old and old.updated_at:
            elapsed = (timestamp - old.updated_at).total_seconds()
            if elapsed > 0:
                delta_in = int(iface.get("in_octets") or 0) - (old.in_octets or 0)
                delta_out = int(iface.get("out_octets") or 0) - (old.out_octets or 0)
                if delta_in >= 0:
                    in_bps = int(delta_in * 8 / elapsed)
                if delta_out >= 0:
                    out_bps = int(delta_out * 8 / elapsed)
        db.add(
            SnmpIface(
                host_id=host.id,
                ifindex=ifindex,
                name=str(iface.get("name", ""))[:128],
                status=int(iface.get("status") or 0),
                speed_mbps=int(iface.get("speed_mbps") or 0),
                in_octets=int(iface.get("in_octets") or 0),
                out_octets=int(iface.get("out_octets") or 0),
                in_bps=in_bps,
                out_bps=out_bps,
                updated_at=timestamp,
            )
        )
        db.add(
            SnmpIfHistory(
                host_id=host.id,
                ifindex=ifindex,
                ts=timestamp,
                in_bps=in_bps,
                out_bps=out_bps,
            )
        )


def poll_loop(stop, interval: int = 60) -> None:
    if settings.dry_run:
        return
    from ..db import SessionLocal

    while not stop.wait(10):
        try:
            with SessionLocal() as db:
                hosts = list(db.scalars(select(SnmpHost).where(SnmpHost.enabled == 1)).all())
                for host in hosts:
                    apply_poll(db, host, poll_host(host))
                db.execute(
                    delete(SnmpIfHistory).where(SnmpIfHistory.ts < now() - timedelta(hours=24))
                )
                db.commit()
        except Exception:
            pass
        if stop.wait(interval):
            break
