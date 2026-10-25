import argparse
import secrets

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import AdminUser, utcnow
from .security import hash_password


def init_admin(username: str, password: str, force: bool) -> int:
    with SessionLocal() as db:
        row = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if row and not force:
            print(f"管理员 {username} 已存在，跳过")
            return 0
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        if row:
            row.password_hash = hash_password(password)
        else:
            db.add(AdminUser(username=username, password_hash=hash_password(password), created_at=utcnow()))
        db.commit()
    print(f"管理员 {username} 已就绪")
    if generated:
        print(f"ADMIN_PASSWORD={password}")
    return 0


def set_password(username: str, password: str) -> int:
    with SessionLocal() as db:
        row = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if row is None:
            print(f"管理员 {username} 不存在")
            return 1
        row.password_hash = hash_password(password)
        db.commit()
    print(f"管理员 {username} 密码已更新")
    return 0


def prune_syslog(days: int) -> int:
    from .services import syslog

    removed, message = syslog.prune(days)
    print(message)
    return 0


def bootstrap() -> int:
    from .db import SessionLocal
    from .models import FtpUser
    from .services import dns, ftp, ntp, radius, snmp, syslog
    from .services.store import load, save

    with SessionLocal() as db:
        for name, module in (("dns", dns), ("ftp", ftp), ("syslog", syslog),
                             ("snmp", snmp), ("ntp", ntp)):
            cfg = load(db, name, module.DEFAULTS)
            save(db, name, cfg)
            if name == "ftp":
                users = [u.username for u in db.scalars(select(FtpUser)).all()]
                result = module.apply_config(cfg, users)
            else:
                result = module.apply_config(cfg)
            print(f"{name}: {'OK' if result.ok else 'FAIL'} {result.text()}")
        result = radius.apply_config(db)
        print(f"radius: {'OK' if result.ok else 'FAIL'} {result.text()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="svcadmin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-admin")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("set-password")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", required=True)

    p = sub.add_parser("prune-syslog")
    p.add_argument("--days", type=int, default=30)

    sub.add_parser("bootstrap")

    args = parser.parse_args(argv)
    init_db()
    if args.cmd == "init-admin":
        return init_admin(args.username, args.password, args.force)
    if args.cmd == "set-password":
        return set_password(args.username, args.password)
    if args.cmd == "bootstrap":
        return bootstrap()
    return prune_syslog(args.days)


if __name__ == "__main__":
    raise SystemExit(main())
