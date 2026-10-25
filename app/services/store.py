import json

from ..models import AuditLog, ServiceConfig


def load(db, service: str, defaults: dict) -> dict:
    data = dict(defaults)
    row = db.get(ServiceConfig, service)
    if row and row.data:
        try:
            stored = json.loads(row.data)
            if isinstance(stored, dict):
                data.update(stored)
        except json.JSONDecodeError:
            pass
    return data


def save(db, service: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    row = db.get(ServiceConfig, service)
    if row is None:
        db.add(ServiceConfig(service=service, data=payload))
    else:
        row.data = payload
    db.commit()


def audit(db, username: str, service: str, action: str, detail: str = "", result: str = "ok") -> None:
    db.add(
        AuditLog(
            username=username or "-",
            service=service,
            action=action,
            detail=(detail or "")[:4000],
            result=result,
        )
    )
    db.commit()
