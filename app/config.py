import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    app_name = "SVC Admin"
    dry_run = os.environ.get("SVCADMIN_DRY_RUN", "0") == "1"
    database_url = os.environ.get(
        "SVCADMIN_DATABASE_URL",
        "sqlite:///" + str(BASE_DIR / "dev.db").replace("\\", "/"),
    )
    radius_database_url = os.environ.get("SVCADMIN_RADIUS_URL", database_url)
    logs_database_url = os.environ.get("SVCADMIN_LOGS_URL", database_url)
    secret_key = os.environ.get("SVCADMIN_SECRET_KEY", "dev-only-insecure-secret")
    staging_dir = Path(os.environ.get("SVCADMIN_STAGING", str(BASE_DIR / "staging")))
    apply_helper = os.environ.get("SVCADMIN_APPLY_HELPER", "/usr/local/sbin/svcadmin-apply")
    session_cookie = "svcadmin_session"
    session_max_age = 12 * 3600
    insecure_cookies = os.environ.get("SVCADMIN_INSECURE_COOKIES", "0") == "1"
    timezone = os.environ.get("SVCADMIN_TIMEZONE", "Asia/Shanghai")
    ftp_uid = int(os.environ.get("SVCADMIN_FTP_UID", "2001"))
    ftp_gid = int(os.environ.get("SVCADMIN_FTP_GID", "2001"))
    db_password = os.environ.get("SVCADMIN_DB_PASSWORD", "")
    db_name = os.environ.get("SVCADMIN_DB_NAME", "svcadmin")
    rsyslog_db_password = os.environ.get("SVCADMIN_RSYSLOG_DB_PASSWORD", "")


settings = Settings()
