import math
import os
import random
import shutil
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete

from ..config import settings
from ..models import SystemSample, now

_last_cpu: tuple[int, int] | None = None
_last_net: tuple[int, int, float] | None = None
_dry_tick = 0


def _cpu_times() -> tuple[int, int]:
    line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
    values = [int(value) for value in line.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(":")
        try:
            info[key] = int(rest.strip().split()[0])
        except (ValueError, IndexError):
            continue
    return info


def _net_totals() -> tuple[int, int]:
    rx = tx = 0
    for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]:
        name, _, data = line.partition(":")
        if name.strip() == "lo":
            continue
        fields = data.split()
        if len(fields) >= 9:
            rx += int(fields[0])
            tx += int(fields[8])
    return rx, tx


def uptime_seconds() -> float:
    if settings.dry_run:
        return 86400 * 3 + 3600 * 5
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError):
        return 0.0


def sample() -> dict:
    global _last_cpu, _last_net, _dry_tick

    if settings.dry_run:
        _dry_tick += 1
        mem_total = 3_960_000
        disk_total = 20_460_000
        return {
            "cpu_pct": round(18 + 12 * math.sin(_dry_tick / 6) + random.random() * 3, 1),
            "mem_used_kb": int(1_300_000 + 250_000 * math.sin(_dry_tick / 9)),
            "mem_total_kb": mem_total,
            "swap_used_kb": 0,
            "swap_total_kb": 3_960_000,
            "disk_used_kb": int(8_000_000 + 20_000 * math.sin(_dry_tick / 12)),
            "disk_total_kb": disk_total,
            "load1": round(0.4 + 0.2 * math.sin(_dry_tick / 7), 2),
            "net_rx_bps": int(900_000 + 600_000 * math.sin(_dry_tick / 5)),
            "net_tx_bps": int(220_000 + 120_000 * math.cos(_dry_tick / 4)),
        }

    total, idle = _cpu_times()
    cpu_pct = 0.0
    if _last_cpu:
        delta_total = total - _last_cpu[0]
        delta_idle = idle - _last_cpu[1]
        if delta_total > 0:
            cpu_pct = max(0.0, min(100.0, 100.0 * (delta_total - delta_idle) / delta_total))
    _last_cpu = (total, idle)

    mem = _meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_avail = mem.get("MemAvailable", mem.get("MemFree", 0))
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)

    try:
        usage = shutil.disk_usage("/")
        disk_total = usage.total // 1024
        disk_used = usage.used // 1024
    except OSError:
        disk_total = disk_used = 0

    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0

    rx, tx = _net_totals()
    stamp = time.time()
    rx_bps = tx_bps = 0
    if _last_net:
        elapsed = stamp - _last_net[2]
        if elapsed > 0:
            delta_rx = rx - _last_net[0]
            delta_tx = tx - _last_net[1]
            if delta_rx >= 0:
                rx_bps = int(delta_rx * 8 / elapsed)
            if delta_tx >= 0:
                tx_bps = int(delta_tx * 8 / elapsed)
    _last_net = (rx, tx, stamp)

    return {
        "cpu_pct": round(cpu_pct, 1),
        "mem_used_kb": max(mem_total - mem_avail, 0),
        "mem_total_kb": mem_total,
        "swap_used_kb": max(swap_total - swap_free, 0),
        "swap_total_kb": swap_total,
        "disk_used_kb": disk_used,
        "disk_total_kb": disk_total,
        "load1": round(load1, 2),
        "net_rx_bps": rx_bps,
        "net_tx_bps": tx_bps,
    }


def sysmon_loop(stop, interval: int = 10) -> None:
    if settings.dry_run:
        return
    from ..db import SessionLocal

    while not stop.wait(3):
        try:
            with SessionLocal() as db:
                db.add(SystemSample(ts=now(), **sample()))
                db.execute(delete(SystemSample).where(SystemSample.ts < now() - timedelta(hours=2)))
                db.commit()
        except Exception:
            pass
        if stop.wait(interval):
            break
