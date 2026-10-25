from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    def add_missing(table: str, columns: dict[str, str]) -> None:
        if table not in tables:
            return
        existing = {column["name"] for column in inspect(engine).get_columns(table)}
        pending = {name: ddl for name, ddl in columns.items() if name not in existing}
        if not pending:
            return
        with engine.begin() as conn:
            for name, ddl in pending.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    add_missing("admin_user", {"role": "VARCHAR(16) NOT NULL DEFAULT 'admin'"})
    add_missing(
        "snmp_host",
        {
            "mem_total_kb": "INTEGER NOT NULL DEFAULT 0",
            "mem_avail_kb": "INTEGER NOT NULL DEFAULT 0",
            "swap_total_kb": "INTEGER NOT NULL DEFAULT 0",
            "swap_avail_kb": "INTEGER NOT NULL DEFAULT 0",
        },
    )
