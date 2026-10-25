"""本地开发/演示入口：dry-run 模式，不接触任何系统服务。"""

import math
import os
import sys
from datetime import timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.environ.setdefault("SVCADMIN_DRY_RUN", "1")
os.environ.setdefault("SVCADMIN_DATABASE_URL", "sqlite:///" + str(BASE / "dev.db"))
os.environ.setdefault("SVCADMIN_SECRET_KEY", "dev-only-insecure-secret")
os.environ.setdefault("SVCADMIN_INSECURE_COOKIES", "1")
os.environ.setdefault("SVCADMIN_STAGING", str(BASE / "staging"))

import uvicorn  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminUser, FtpUser, SystemSample, now  # noqa: E402
from app.security import hash_password  # noqa: E402

init_db()
with SessionLocal() as db:
    if db.scalar(select(AdminUser).where(AdminUser.username == "admin")) is None:
        db.add(AdminUser(username="admin", password_hash=hash_password("devadmin123")))
        print("演示账号：admin / devadmin123")
    if db.scalar(select(FtpUser).where(FtpUser.username == "deploy")) is None:
        db.add(
            FtpUser(
                username="deploy",
                password="not-used-in-dry-run",
                uid=settings.ftp_uid,
                gid=settings.ftp_gid,
                homedir="/srv/ftp/deploy",
                quota_mb=1024,
                status=1,
            )
        )
    if db.scalar(select(SystemSample).limit(1)) is None:
        base = now() - timedelta(minutes=15)
        for index in range(90):
            db.add(
                SystemSample(
                    ts=base + timedelta(seconds=10 * index),
                    cpu_pct=round(22 + 12 * math.sin(index / 7), 1),
                    mem_used_kb=int(1_400_000 + 200_000 * math.sin(index / 11)),
                    mem_total_kb=3_960_000,
                    swap_used_kb=0,
                    swap_total_kb=3_960_000,
                    disk_used_kb=8_100_000,
                    disk_total_kb=20_460_000,
                    load1=round(0.45 + 0.25 * math.sin(index / 9), 2),
                    net_rx_bps=int(900_000 + 700_000 * math.sin(index / 6)),
                    net_tx_bps=int(240_000 + 150_000 * math.cos(index / 5)),
                )
            )
    db.commit()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="info")
