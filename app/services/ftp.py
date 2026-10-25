import subprocess

from ..config import settings
from .base import ApplyResult, FileSpec, apply_service

UNIT = "pure-ftpd-mysql"
MYSQL_CONF = "/etc/pure-ftpd/db/mysql.conf"
CONF_DIR = "/etc/pure-ftpd/conf"
FTP_ROOT = "/srv/ftp"

DEFAULTS = {
    "enabled": True,
    "tls": True,
    "require_tls": False,
    "passive_ports": "39000:40000",
    "max_clients": 20,
    "max_per_ip": 5,
}

CERT_PATH = "/etc/ssl/private/pure-ftpd.pem"


def hash_password(password: str) -> str:
    if settings.dry_run:
        return "$6$dryrun$" + "x" * 86
    proc = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=password,
        capture_output=True,
        text=True,
        timeout=10,
    )
    digest = proc.stdout.strip()
    if proc.returncode != 0 or not digest:
        raise RuntimeError(f"openssl passwd 失败：{proc.stderr.strip()}")
    return digest


def render_mysql_conf() -> str:
    pw = settings.db_password
    db = settings.db_name
    where = "username='\\L' AND status=1"
    return f"""# Managed by SVC Admin
MYSQLSocket      /run/mysqld/mysqld.sock
MYSQLUser        svcadmin
MYSQLPassword    {pw}
MYSQLDatabase    {db}
MYSQLCrypt       crypt
MYSQLGetPW       SELECT password FROM ftp_user WHERE {where}
MYSQLGetUID      SELECT uid FROM ftp_user WHERE {where}
MYSQLGetGID      SELECT gid FROM ftp_user WHERE {where}
MYSQLGetDir      SELECT homedir FROM ftp_user WHERE {where}
MySQLGetQTASZ    SELECT quota_mb FROM ftp_user WHERE {where}
MySQLGetBandwidthUL SELECT ul_bandwidth FROM ftp_user WHERE {where}
MySQLGetBandwidthDL SELECT dl_bandwidth FROM ftp_user WHERE {where}
"""


def _conf(name: str, value: str) -> FileSpec:
    return FileSpec(f"{CONF_DIR}/{name}", value + "\n", mode="0644")


def render_files(cfg: dict) -> list[FileSpec]:
    files = [
        _conf("NoAnonymous", "yes"),
        _conf("ChrootEveryone", "yes"),
        _conf("PAMAuthentication", "no"),
        _conf("UnixAuthentication", "no"),
        _conf("FSCharset", "UTF-8"),
        _conf("MinUID", "1000"),
        _conf("MaxClientsNumber", str(int(cfg.get("max_clients") or 20))),
        _conf("MaxClientsPerIP", str(int(cfg.get("max_per_ip") or 5))),
        _conf("PassivePortRange", str(cfg.get("passive_ports") or "39000 40000").replace(":", " ")),
        _conf("MySQLConfigFile", MYSQL_CONF),
        FileSpec(MYSQL_CONF, render_mysql_conf(), mode="0600"),
    ]
    if cfg.get("tls", True):
        files.append(_conf("TLS", "2" if cfg.get("require_tls") else "1"))
    else:
        files.append(_conf("TLS", "0"))
    return files


def apply_config(cfg: dict, usernames: list[str]) -> ApplyResult:
    enabled = bool(cfg.get("enabled", True))
    return apply_service(
        "ftp",
        files=render_files(cfg),
        restart=["systemctl", "restart", UNIT] if enabled else ["systemctl", "stop", UNIT],
        health=["systemctl", "is-active", UNIT] if enabled else [],
        health_ports=[21] if enabled else [],
        actions=[f"ftp_home:{name}" for name in usernames],
    )


def test_login(username: str, password: str, use_tls: bool = True) -> str:
    if settings.dry_run:
        return "dry-run：跳过登录测试"
    from ftplib import FTP, FTP_TLS

    client = None
    try:
        if use_tls:
            client = FTP_TLS(timeout=8)
            client.connect("127.0.0.1", 21)
            client.login(username, password)
            client.prot_p()
        else:
            client = FTP(timeout=8)
            client.connect("127.0.0.1", 21)
            client.login(username, password)
        client.quit()
        return "登录成功"
    except Exception as exc:
        return f"登录失败：{exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
