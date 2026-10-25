from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog
from ..security import require_user
from ..web import render

router = APIRouter()


@router.get("/audit")
async def audit_page(request: Request, db: Session = Depends(get_db)):
    require_user(request)
    page = max(int(request.query_params.get("page", "1") or 1), 1)
    service = request.query_params.get("service", "")
    limit = 60
    query = select(AuditLog).order_by(AuditLog.id.desc())
    if service:
        query = query.where(AuditLog.service == service)
    rows = list(db.scalars(query.offset((page - 1) * limit).limit(limit)))
    return render(
        request,
        "audit.html",
        active="audit",
        rows=rows,
        page=page,
        service=service,
        has_next=len(rows) == limit,
    )
