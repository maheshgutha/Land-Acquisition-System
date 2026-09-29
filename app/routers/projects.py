from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, workflow
from ..config import ALLOWED_UPLOAD_EXTENSIONS, MAX_UPLOAD_MB
from ..db import get_db
from ..ml.features import STATES
from ..models import Award, Document, Event, Notification, Parcel, Project, RiskScore, User, utcnow
from ..security import get_current_user, get_project_or_404, require_roles, scoped_projects
from ..services import risk_service
from ..services.metrics import build_metrics
from ..services.serialize import project_row, risk_dict
from ..workflow import ADVANCE_ROLES, REQUIRED_DOC_CATEGORIES, STAGE_LABELS, STALL_REASONS, WorkflowError

router = APIRouter(prefix="/api", tags=["projects"])

STATE_CODES = {
    "Andhra Pradesh": "AP", "Telangana": "TS", "Karnataka": "KA", "Maharashtra": "MH", "Tamil Nadu": "TN",
    "Kerala": "KL", "Odisha": "OD", "West Bengal": "WB", "Uttar Pradesh": "UP", "Rajasthan": "RJ",
    "Madhya Pradesh": "MP", "Bihar": "BR", "Gujarat": "GJ",
}


class ProjectIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    project_type: Literal["highway", "railway", "solar_park", "irrigation", "urban", "industrial_corridor"]
    state: str
    district: str
    agency: Optional[str] = None
    proposed_area_ha: float = Field(gt=0)
    estimated_cost_cr: float = Field(ge=0)
    planned_completion: Optional[date] = None
    template_key: Optional[str] = None


class ProjectPatch(BaseModel):
    legal_disputes: Optional[int] = Field(default=None, ge=0, le=50)
    approvals_pending: Optional[int] = Field(default=None, ge=0, le=50)
    avg_response_days: Optional[float] = Field(default=None, ge=0, le=365)


class StallIn(BaseModel):
    reason: str
    note: str = Field(default="", max_length=1000)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _workflow_http(e: WorkflowError) -> HTTPException:
    return HTTPException(e.status_code, detail={"message": e.message, "unmet": e.unmet})


def _event_dict(e: Event) -> dict:
    return {
        "id": e.id, "kind": e.kind, "stage": e.stage, "stage_label": STAGE_LABELS.get(e.stage or "", None),
        "detail": e.detail, "actor": e.actor, "at": e.at.isoformat(),
    }


def _rescore(db: Session, project: Project) -> None:
    risk_service.score_projects(db, [project])


def _detail(db: Session, user: User, project: Project) -> dict:
    m = build_metrics(db, [project])[project.id]
    keys = workflow.plan_keys(project.template_key)
    issues = workflow.gate_issues(db, project)
    is_last = project.stage == keys[-1]
    allowed = user.role in ADVANCE_ROLES.get(project.stage, set())
    events = db.scalars(
        select(Event).where(Event.project_id == project.id).order_by(Event.id.desc()).limit(40)
    ).all()
    latest = m["risk"]
    history = db.execute(
        select(RiskScore.scored_at, RiskScore.score, RiskScore.stage)
        .where(RiskScore.project_id == project.id)
        .order_by(RiskScore.id.desc())
        .limit(12)
    ).all()
    counts = {
        "parcels": m["parcels"],
        "notifications": db.scalar(select(func.count()).select_from(Notification).where(Notification.project_id == project.id)),
        "awards": db.scalar(select(func.count()).select_from(Award).where(Award.project_id == project.id)),
        "documents": db.scalar(select(func.count()).select_from(Document).where(Document.project_id == project.id)),
    }
    return {
        **project_row(project, m),
        "template_key": project.template_key,
        "estimated_cost_cr": project.estimated_cost_cr,
        "created_at": project.created_at.isoformat(),
        "planned_completion": project.planned_completion.isoformat() if project.planned_completion else None,
        "approvals_pending": project.approvals_pending,
        "avg_response_days": project.avg_response_days,
        "stall_note": project.stall_note,
        "stalled_since": project.stalled_since.isoformat() if project.stalled_since else None,
        "stages": workflow.stage_view(db, project),
        "gate_issues": issues,
        "advance_roles": sorted(ADVANCE_ROLES.get(project.stage, [])),
        "can_advance": bool(allowed and not issues and not is_last),
        "counts": counts,
        "events": [_event_dict(e) for e in events],
        "risk": risk_dict(latest, full=True),
        "risk_history": [{"at": a.isoformat(), "score": s, "stage": st} for a, s, st in history],
        "documents_required": REQUIRED_DOC_CATEGORIES,
        "documents_uploaded": sorted(m["doc_cats"]),
        "rr_status_counts": dict(m["rr_status_counts"]),
    }


@router.get("/meta")
def meta(user: User = Depends(get_current_user)):
    return {
        "stages": [{"key": k, "label": l, "default_days": d} for k, l, d in workflow.STAGES],
        "stall_reasons": STALL_REASONS,
        "states": STATES,
        "advance_roles": {k: sorted(v) for k, v in ADVANCE_ROLES.items()},
        "templates": list(workflow.TEMPLATES),
        "required_documents": REQUIRED_DOC_CATEGORIES,
        "upload": {"allowed_extensions": sorted(ALLOWED_UPLOAD_EXTENSIONS), "max_mb": MAX_UPLOAD_MB},
    }


@router.get("/projects")
def list_projects(
    state: Optional[str] = None,
    district: Optional[str] = None,
    stage: Optional[str] = None,
    status: Optional[str] = None,
    project_type: Optional[str] = None,
    risk: Optional[str] = Query(default=None, description="low|medium|high|critical"),
    q: Optional[str] = Query(default=None, description="search name or code"),
    sort: Literal["risk", "progress", "name", "days"] = "risk",
    limit: int = Query(default=200, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = scoped_projects(user)
    if state:
        query = query.where(Project.state == state)
    if district:
        query = query.where(Project.district == district)
    if stage:
        query = query.where(Project.stage == stage)
    if status:
        query = query.where(Project.status == status)
    if project_type:
        query = query.where(Project.project_type == project_type)
    if q:
        like = f"%{q}%"
        query = query.where(Project.name.ilike(like) | Project.code.ilike(like))
    projects = list(db.scalars(query))
    metrics = build_metrics(db, projects)
    rows = [project_row(p, metrics[p.id]) for p in projects]
    if risk:
        rows = [r for r in rows if r["risk_category"] == risk]
    keyfn = {
        "risk": lambda r: (-(r["risk_score"] if r["risk_score"] is not None else -1), -r["overrun_ratio"]),
        "progress": lambda r: -r["progress_pct"],
        "name": lambda r: r["name"].lower(),
        "days": lambda r: -r["days_in_stage"],
    }[sort]
    rows.sort(key=keyfn)
    return {"total": len(rows), "items": rows[:limit]}


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectIn,
    user: User = Depends(require_roles("agency", "state")),
    db: Session = Depends(get_db),
):
    if user.role == "state" and body.state != user.state:
        raise HTTPException(403, "State users can only create projects in their own state")
    agency = user.agency if user.role == "agency" else (body.agency or "").strip()
    if not agency:
        raise HTTPException(422, "agency is required")
    template = body.template_key or workflow.template_key_for_state(body.state)
    if template not in workflow.TEMPLATES:
        raise HTTPException(422, f"Unknown template '{template}'")
    next_id = db.scalar(select(func.coalesce(func.max(Project.id), 0) + 1))
    now = utcnow()
    project = Project(
        code=f"LA-{STATE_CODES.get(body.state, 'XX')}-{now.year}-{next_id:04d}",
        name=body.name.strip(), project_type=body.project_type, agency=agency, state=body.state,
        district=body.district, template_key=template, stage="proposal", stage_entered_at=now,
        proposed_area_ha=body.proposed_area_ha, estimated_cost_cr=body.estimated_cost_cr,
        planned_completion=body.planned_completion, created_at=now,
    )
    db.add(project)
    db.flush()
    workflow.build_milestones(db, project, now)
    db.add(Event(project_id=project.id, kind="created", stage="proposal", actor=user.username, at=now,
                 detail=f"Proposal created by {agency}"))
    audit.log(db, user, "project_created", "project", project.id, {"code": project.code, "template": template})
    _rescore(db, project)
    db.commit()
    return _detail(db, user, project)


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _detail(db, user, get_project_or_404(db, user, project_id))


@router.post("/projects/{project_id}/advance")
def advance_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    try:
        workflow.advance(db, project, user)
    except WorkflowError as e:
        db.rollback()
        raise _workflow_http(e)
    _rescore(db, project)
    db.commit()
    return _detail(db, user, project)


@router.post("/projects/{project_id}/stall")
def stall_project(
    project_id: int, body: StallIn,
    user: User = Depends(require_roles("district", "state", "field", "central")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    try:
        workflow.stall(db, project, user, body.reason, body.note)
    except WorkflowError as e:
        db.rollback()
        raise _workflow_http(e)
    _rescore(db, project)
    db.commit()
    return _detail(db, user, project)


@router.post("/projects/{project_id}/resume")
def resume_project(
    project_id: int, body: NoteIn | None = None,
    user: User = Depends(require_roles("district", "state", "central")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    try:
        workflow.resume(db, project, user, body.text if body else "")
    except WorkflowError as e:
        db.rollback()
        raise _workflow_http(e)
    _rescore(db, project)
    db.commit()
    return _detail(db, user, project)


@router.post("/projects/{project_id}/notes", status_code=201)
def add_note(
    project_id: int, body: NoteIn,
    user: User = Depends(require_roles("central", "state", "district", "field", "agency")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    ev = Event(project_id=project.id, kind="note", stage=project.stage, detail=body.text, actor=user.username)
    db.add(ev)
    audit.log(db, user, "note_added", "project", project.id, {"length": len(body.text)})
    db.commit()
    return _event_dict(ev)


@router.patch("/projects/{project_id}")
def patch_project(
    project_id: int, body: ProjectPatch,
    user: User = Depends(require_roles("district", "state", "field")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to update")
    before = {k: getattr(project, k) for k in changes}
    for k, v in changes.items():
        setattr(project, k, v)
    db.add(Event(project_id=project.id, kind="update", stage=project.stage, actor=user.username,
                 detail="Updated " + ", ".join(f"{k}: {before[k]} -> {v}" for k, v in changes.items())))
    audit.log(db, user, "project_updated", "project", project.id, {"before": before, "after": changes})
    _rescore(db, project)
    db.commit()
    return _detail(db, user, project)


@router.get("/projects/{project_id}/why")
def why_delayed(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Answers 'why is this project stuck?' strictly from recorded facts, rules and model drivers.

    Recorded facts, rule-based findings and model inference are returned separately so nobody
    mistakes a prediction for a record. If nothing explains the delay, it says so.
    """
    project = get_project_or_404(db, user, project_id)
    m = build_metrics(db, [project])[project.id]
    label = STAGE_LABELS.get(project.stage, project.stage)
    recorded, findings, inference = [], [], []

    if project.status == "stalled":
        reason = STALL_REASONS.get(project.stall_reason or "", "no reason selected")
        since = project.stalled_since.date().isoformat() if project.stalled_since else "unknown date"
        recorded.append({"text": f"Marked stalled on {since}: {reason}." + (f" Note: {project.stall_note}" if project.stall_note else ""),
                         "source": "project.stall_reason"})
    for e in db.scalars(select(Event).where(Event.project_id == project.id, Event.kind.in_(["stalled", "dispute", "note"]))
                        .order_by(Event.id.desc()).limit(5)):
        recorded.append({"text": f"{e.at.date().isoformat()} ({e.actor}): {e.detail}", "source": f"event #{e.id}"})

    if m["planned_days"] and m["days_in_stage"] > m["planned_days"]:
        findings.append(f"'{label}' has taken {m['days_in_stage']:.0f} days against {m['planned_days']} planned.")
    if project.legal_disputes:
        findings.append(f"{project.legal_disputes} open legal dispute(s) are recorded.")
    if m["disputed_parcels"]:
        findings.append(f"{m['disputed_parcels']} parcel(s) are flagged with ownership disputes.")
    if project.approvals_pending:
        findings.append(f"{project.approvals_pending} inter-department approval(s) are pending.")
    unmet = workflow.gate_issues(db, project) if project.status == "active" else []
    findings.extend(f"Cannot advance: {u}." for u in unmet)

    rs = m["risk"]
    recs = []
    if rs:
        inference = [
            f"{d['label']} (value {d['value']}) raises the predicted delay risk."
            for d in rs.drivers if d["direction"] == "increases"
        ][:4]
        recs = rs.recommendations

    if recorded or findings:
        summary = f"{project.code} is at '{label}'. " + (
            "A reason is recorded." if recorded else "No stop reason is recorded, but the data shows the issues below."
        )
    else:
        summary = f"No delay reason is recorded for {project.code} and no rule flags a problem at '{label}'."
    return {
        "project_id": project.id, "summary": summary, "has_recorded_reason": bool(recorded),
        "recorded_facts": recorded, "rule_findings": findings,
        "model_inference": inference, "recommendations": recs,
        "note": "Model inference is a prediction from patterns in data, not a recorded cause.",
    }
