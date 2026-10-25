import secrets

from sqlalchemy import create_engine, text

from ..config import settings
from .base import ApplyResult, apply_service, run_command

UNIT = "freeradius"
TEST_NAS = "127.0.0.1"

DEFAULTS = {"enabled": True, "test_secret": ""}

_engine = None


def _eng():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.radius_database_url, pool_pre_ping=True)
    return _engine


def ensure_test_client(db) -> tuple[str, bool]:
    from .store import load, save

    cfg = load(db, "radius", DEFAULTS)
    if not cfg.get("test_secret"):
        cfg["test_secret"] = secrets.token_urlsafe(18)
        save(db, "radius", cfg)
    secret = cfg["test_secret"]
    created = False
    if not settings.dry_run:
        existing = _rows("SELECT id FROM nas WHERE nasname=:n", {"n": TEST_NAS})
        if not existing:
            with _eng().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO nas (nasname, shortname, type, secret, description) "
                        "VALUES (:n, 'svcadmin-test', 'other', :s, 'SVC Admin 自测客户端')"
                    ),
                    {"n": TEST_NAS, "s": secret},
                )
            created = True
    return secret, created


def apply_config(db) -> ApplyResult:
    from .store import load

    cfg = load(db, "radius", DEFAULTS)
    if not cfg.get("enabled", True):
        return apply_service("radius", files=[], restart=["systemctl", "stop", UNIT])
    return apply_service(
        "radius",
        files=[],
        restart=["systemctl", "restart", UNIT],
        health=["systemctl", "is-active", UNIT],
    )


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with _eng().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]


def list_nas() -> list[dict]:
    if settings.dry_run:
        return [
            {"id": 1, "nasname": "10.10.10.254", "shortname": "gw", "type": "other",
             "secret": "demo-secret", "description": "dry-run 示例 NAS"}
        ]
    return _rows("SELECT id, nasname, shortname, type, secret, description FROM nas ORDER BY id")


def save_nas(nas: dict) -> None:
    if settings.dry_run:
        return
    with _eng().begin() as conn:
        if nas.get("id"):
            conn.execute(
                text(
                    "UPDATE nas SET nasname=:nasname, shortname=:shortname, type=:type, "
                    "secret=:secret, description=:description WHERE id=:id"
                ),
                nas,
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO nas (nasname, shortname, type, secret, description) "
                    "VALUES (:nasname, :shortname, :type, :secret, :description)"
                ),
                nas,
            )


def delete_nas(nas_id: int) -> None:
    if settings.dry_run:
        return
    with _eng().begin() as conn:
        conn.execute(text("DELETE FROM nas WHERE id=:id"), {"id": nas_id})


def list_users() -> list[dict]:
    if settings.dry_run:
        return [{"username": "demo", "groups": "staff", "attrs": 2}]
    return _rows(
        "SELECT c.username, "
        "(SELECT GROUP_CONCAT(groupname) FROM radusergroup g WHERE g.username=c.username) AS groups, "
        "(SELECT COUNT(*) FROM radreply r WHERE r.username=c.username) AS attrs "
        "FROM radcheck c GROUP BY c.username ORDER BY c.username"
    )


def get_user(username: str) -> dict:
    if settings.dry_run:
        return {
            "username": username,
            "password": "",
            "groups": "staff",
            "check_attrs": "",
            "reply_attrs": "Framed-IP-Address = 10.10.10.50",
        }
    check = _rows(
        "SELECT attribute, op, value FROM radcheck WHERE username=:u ORDER BY id", {"u": username}
    )
    reply = _rows(
        "SELECT attribute, op, value FROM radreply WHERE username=:u ORDER BY id", {"u": username}
    )
    groups = _rows(
        "SELECT groupname FROM radusergroup WHERE username=:u ORDER BY priority", {"u": username}
    )
    password = ""
    extra = []
    for row in check:
        if row["attribute"] in ("Cleartext-Password", "User-Password"):
            password = row["value"]
        else:
            extra.append(f"{row['attribute']} {row['op']} {row['value']}")
    return {
        "username": username,
        "password": password,
        "groups": ",".join(g["groupname"] for g in groups),
        "check_attrs": "\n".join(extra),
        "reply_attrs": "\n".join(f"{r['attribute']} {r['op']} {r['value']}" for r in reply),
    }


def _parse_attrs(raw: str) -> list[tuple[str, str, str]]:
    result = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        op = ":=" if ":=" in line else "="
        attr, _, value = line.replace(":=", "=").partition("=")
        attr, value = attr.strip(), value.strip()
        if attr and value:
            result.append((attr, op, value))
    return result


def save_user(username: str, password: str, groups: str, check_attrs: str, reply_attrs: str) -> None:
    if settings.dry_run:
        return
    with _eng().begin() as conn:
        if not password:
            password = conn.execute(
                text(
                    "SELECT value FROM radcheck WHERE username=:u AND attribute='Cleartext-Password' LIMIT 1"
                ),
                {"u": username},
            ).scalar() or ""
        conn.execute(text("DELETE FROM radcheck WHERE username=:u"), {"u": username})
        conn.execute(text("DELETE FROM radreply WHERE username=:u"), {"u": username})
        conn.execute(text("DELETE FROM radusergroup WHERE username=:u"), {"u": username})
        if password:
            conn.execute(
                text(
                    "INSERT INTO radcheck (username, attribute, op, value) "
                    "VALUES (:u, 'Cleartext-Password', ':=', :v)"
                ),
                {"u": username, "v": password},
            )
        for attr, op, value in _parse_attrs(check_attrs):
            conn.execute(
                text(
                    "INSERT INTO radcheck (username, attribute, op, value) "
                    "VALUES (:u, :a, :o, :v)"
                ),
                {"u": username, "a": attr, "o": op, "v": value},
            )
        for attr, op, value in _parse_attrs(reply_attrs):
            conn.execute(
                text(
                    "INSERT INTO radreply (username, attribute, op, value) "
                    "VALUES (:u, :a, :o, :v)"
                ),
                {"u": username, "a": attr, "o": op, "v": value},
            )
        for group in [g.strip() for g in (groups or "").split(",") if g.strip()]:
            conn.execute(
                text(
                    "INSERT INTO radusergroup (username, groupname, priority) "
                    "VALUES (:u, :g, 1)"
                ),
                {"u": username, "g": group},
            )


def delete_user(username: str) -> None:
    if settings.dry_run:
        return
    with _eng().begin() as conn:
        for table in ("radcheck", "radreply", "radusergroup"):
            conn.execute(text(f"DELETE FROM {table} WHERE username=:u"), {"u": username})


def list_groups() -> list[dict]:
    if settings.dry_run:
        return [{"groupname": "staff", "attrs": 1}]
    return _rows(
        "SELECT g.groupname, "
        "(SELECT COUNT(*) FROM radgroupreply r WHERE r.groupname=g.groupname) + "
        "(SELECT COUNT(*) FROM radgroupcheck c WHERE c.groupname=g.groupname) AS attrs "
        "FROM (SELECT groupname FROM radgroupcheck UNION SELECT groupname FROM radgroupreply) g "
        "ORDER BY g.groupname"
    )


def get_group(name: str) -> dict:
    if settings.dry_run:
        return {"groupname": name, "check_attrs": "", "reply_attrs": ""}
    check = _rows(
        "SELECT attribute, op, value FROM radgroupcheck WHERE groupname=:g ORDER BY id", {"g": name}
    )
    reply = _rows(
        "SELECT attribute, op, value FROM radgroupreply WHERE groupname=:g ORDER BY id", {"g": name}
    )
    return {
        "groupname": name,
        "check_attrs": "\n".join(f"{r['attribute']} {r['op']} {r['value']}" for r in check),
        "reply_attrs": "\n".join(f"{r['attribute']} {r['op']} {r['value']}" for r in reply),
    }


def save_group(name: str, check_attrs: str, reply_attrs: str) -> None:
    if settings.dry_run:
        return
    with _eng().begin() as conn:
        conn.execute(text("DELETE FROM radgroupcheck WHERE groupname=:g"), {"g": name})
        conn.execute(text("DELETE FROM radgroupreply WHERE groupname=:g"), {"g": name})
        for attr, op, value in _parse_attrs(check_attrs):
            conn.execute(
                text(
                    "INSERT INTO radgroupcheck (groupname, attribute, op, value) "
                    "VALUES (:g, :a, :o, :v)"
                ),
                {"g": name, "a": attr, "o": op, "v": value},
            )
        for attr, op, value in _parse_attrs(reply_attrs):
            conn.execute(
                text(
                    "INSERT INTO radgroupreply (groupname, attribute, op, value) "
                    "VALUES (:g, :a, :o, :v)"
                ),
                {"g": name, "a": attr, "o": op, "v": value},
            )


def delete_group(name: str) -> None:
    if settings.dry_run:
        return
    with _eng().begin() as conn:
        for table in ("radgroupcheck", "radgroupreply"):
            conn.execute(text(f"DELETE FROM {table} WHERE groupname=:g"), {"g": name})


def list_sessions(active_only: bool = False, limit: int = 50) -> list[dict]:
    if settings.dry_run:
        return [
            {"username": "demo", "nasipaddress": "10.10.10.254", "acctstarttime": "2026-10-25 09:00:00",
             "acctstoptime": None, "framedipaddress": "10.10.10.50"}
        ]
    where = "WHERE acctstoptime IS NULL" if active_only else ""
    return _rows(
        "SELECT radacctid, username, nasipaddress, acctstarttime, acctstoptime, "
        f"acctsessiontime, framedipaddress FROM radacct {where} ORDER BY radacctid DESC LIMIT :limit",
        {"limit": limit},
    )


def test_auth(username: str, password: str, secret: str) -> str:
    if settings.dry_run:
        return "dry-run: 跳过认证测试"
    code, output = run_command(
        ["radtest", "-t", "pap", username, password, TEST_NAS, "0", secret], timeout=20
    )
    if "Access-Accept" in output:
        return "认证成功：Access-Accept"
    if "Access-Reject" in output:
        return "认证被拒绝：Access-Reject"
    return f"无有效响应（exit {code}）：{output[:800]}"
