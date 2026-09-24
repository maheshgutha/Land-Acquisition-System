"""Audit trail and the integration gateway (mock land-records lookup)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, integrations
from ..db import get_db
from ..models import AuditLog, User
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/audit")
def audit_list(
    action: Optional[str] = None, entity_type: Optional[str] = None, actor: Optional[str] = None,
    limit: int = Query(default=50, le=500), offset: int = 0,
    user: User = Depends(require_roles("auditor", "central")), db: Session = Depends(get_db),
):
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if actor:
        q = q.where(AuditLog.actor == actor)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset))
    return {
        "total": total,
        "items": [
            {"id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "role": r.role, "action": r.action,
             "entity_type": r.entity_type, "entity_id": r.entity_id, "detail": r.detail, "hash": r.hash[:16]}
            for r in rows
        ],
    }


@router.get("/audit/verify")
def audit_verify(user: User = Depends(require_roles("auditor", "central")), db: Session = Depends(get_db)):
    return audit.verify_chain(db)


@router.get("/integrations/land-records")
def land_records_lookup(
    state: str, district: str, village: str, survey_no: str,
    user: User = Depends(require_roles("central", "state", "district", "field", "auditor")),
    db: Session = Depends(get_db),
):
    """Mock of an external land-records API, called through the gateway so every call is audited."""
    rec = integrations.get_land_records().lookup(state, district, village, survey_no)
    audit.log(db, user, "integration_call", "land_record", survey_no, {"source": rec["source"], "village": village})
    db.commit()
    return rec
