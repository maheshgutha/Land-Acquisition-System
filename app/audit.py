"""Tamper-evident audit trail: every row stores sha256(prev_hash + row content)."""
import hashlib
import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, User, utcnow

GENESIS = "GENESIS"


def _digest(prev: str, ts, actor, role, action, entity_type, entity_id, detail) -> str:
    body = json.dumps(
        {
            "ts": ts.isoformat(),
            "actor": actor,
            "role": role,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "detail": detail,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((prev + body).encode()).hexdigest()


def log(
    db: Session,
    actor: Optional[User | str],
    action: str,
    entity_type: str,
    entity_id: Any = "",
    detail: Optional[dict] = None,
) -> AuditLog:
    if isinstance(actor, User):
        actor_name, role = actor.username, actor.role
    else:
        actor_name, role = (actor or "system"), ""
    # Round-trip through JSON so what is hashed is exactly what is stored.
    clean = json.loads(json.dumps(detail or {}, default=str))
    last = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    prev = last.hash if last else GENESIS
    ts = utcnow()
    row = AuditLog(
        ts=ts,
        actor=actor_name,
        role=role,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        detail=clean,
        prev_hash=prev,
        hash=_digest(prev, ts, actor_name, role, action, entity_type, entity_id, clean),
    )
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session) -> dict:
    """Recompute the whole chain. Reports the first row that does not match."""
    prev = GENESIS
    checked = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        expected = _digest(
            prev, row.ts, row.actor, row.role, row.action, row.entity_type, row.entity_id, row.detail
        )
        if row.prev_hash != prev or row.hash != expected:
            return {"valid": False, "checked": checked, "broken_at": row.id}
        prev = row.hash
        checked += 1
    return {"valid": True, "checked": checked, "broken_at": None}
