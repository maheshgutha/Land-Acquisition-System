"""Configurable acquisition lifecycle: stage plan, role authority, gates, stalls.

The stage list follows the lifecycle named in the problem statement (proposal to
possession and R&R). Per-state templates only change planned durations here; the
`skip` option shows how a state could drop a stage. Durations are illustrative
defaults for the prototype, not legal timelines.
"""
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit
from .models import (
    Award,
    Compensation,
    Document,
    Event,
    Family,
    Milestone,
    Notification,
    Parcel,
    Project,
    RiskScore,
    User,
    utcnow,
)

STAGES = [
    ("proposal", "Proposal submitted", 14),
    ("scrutiny", "Digital scrutiny", 30),
    ("approval", "Approval", 30),
    ("notification", "Land notification", 21),
    ("survey_objections", "Survey and objections", 60),
    ("award", "Award declared", 45),
    ("compensation", "Compensation", 60),
    ("possession", "Possession", 30),
    ("rr", "Rehabilitation and resettlement", 90),
    ("closeout", "Closeout", 0),
]
STAGE_KEYS = [s[0] for s in STAGES]
STAGE_LABELS = {k: label for k, label, _ in STAGES}
DEFAULT_DAYS = {k: d for k, _, d in STAGES}

TEMPLATES = {
    "default": {"days": {}, "skip": []},
    "andhra_pradesh": {"days": {"survey_objections": 45}, "skip": []},
    "telangana": {"days": {"scrutiny": 21, "compensation": 75}, "skip": []},
    "karnataka": {"days": {"approval": 40}, "skip": []},
    "fast_track": {"days": {"scrutiny": 15, "approval": 15}, "skip": ["survey_objections"]},
}

# Who may complete (leave) each stage.
ADVANCE_ROLES = {
    "proposal": {"agency", "state"},
    "scrutiny": {"district", "state"},
    "approval": {"state", "central"},
    "notification": {"district"},
    "survey_objections": {"district", "field"},
    "award": {"district"},
    "compensation": {"district"},
    "possession": {"district", "field"},
    "rr": {"district", "field"},
}

REQUIRED_DOC_CATEGORIES = [
    "proposal_report",
    "land_plan",
    "cost_estimate",
    "environment_clearance",
    "social_impact_assessment",
]

STALL_REASONS = {
    "court_stay": "Court stay or litigation",
    "funding_gap": "Funding or budget gap",
    "clearance_pending": "Statutory clearance pending",
    "protest": "Local protest or law-and-order issue",
    "compensation_dispute": "Compensation dispute",
    "documentation": "Incomplete documentation",
    "admin_bottleneck": "Administrative bottleneck",
    "rr_dispute": "R&R dispute",
    "other": "Other",
}

RR_WEIGHTS = {"pending": 0.0, "package_approved": 0.33, "allotted": 0.66, "resettled": 1.0}
DELAY_TOLERANCE = 1.25  # a stage counts as delayed when it runs > 125% of plan
COMPENSATION_GATE_PCT = 95.0


class WorkflowError(Exception):
    def __init__(self, message: str, unmet: Optional[list[str]] = None, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.unmet = unmet or []
        self.status_code = status_code


def template_key_for_state(state: str) -> str:
    key = state.strip().lower().replace(" ", "_")
    return key if key in TEMPLATES else "default"


def plan(template_key: str) -> list[dict]:
    tpl = TEMPLATES.get(template_key, TEMPLATES["default"])
    out = []
    for key, label, days in STAGES:
        if key in tpl["skip"]:
            continue
        out.append({"key": key, "label": label, "days": tpl["days"].get(key, days)})
    return out


def plan_keys(template_key: str) -> list[str]:
    return [s["key"] for s in plan(template_key)]


def days_in_stage(project: Project, now=None) -> float:
    now = now or utcnow()
    return max(0.0, (now - project.stage_entered_at).total_seconds() / 86400)


def progress_pct(project: Project) -> float:
    if project.status == "completed":
        return 100.0
    keys = plan_keys(project.template_key)
    if project.stage not in keys or len(keys) < 2:
        return 0.0
    return round(100.0 * keys.index(project.stage) / (len(keys) - 1), 1)


def build_milestones(db: Session, project: Project, start) -> None:
    """Create the planned schedule, chained stage after stage from `start`."""
    cursor = start.date()
    for i, step in enumerate(plan(project.template_key)):
        end = cursor + timedelta(days=step["days"])
        db.add(
            Milestone(
                project_id=project.id,
                stage=step["key"],
                planned_days=step["days"],
                planned_start=cursor,
                planned_end=end,
                actual_start=start if i == 0 else None,
            )
        )
        cursor = end
    db.flush()


def _milestone(db: Session, project_id: int, stage: str) -> Optional[Milestone]:
    return db.scalar(select(Milestone).where(Milestone.project_id == project_id, Milestone.stage == stage))


def planned_days_for(db: Session, project: Project) -> int:
    ms = _milestone(db, project.id, project.stage)
    return ms.planned_days if ms else 0


def gate_issues(db: Session, project: Project) -> list[str]:
    """Conditions that must hold before the current stage can be completed."""
    issues: list[str] = []
    if project.status == "stalled":
        issues.append("Project is stalled; resume it before advancing")
    if project.status == "completed":
        issues.append("Project is already completed")
    if issues:
        return issues
    pid, stage = project.id, project.stage

    if stage in ("proposal", "scrutiny"):
        cats = set(db.scalars(select(Document.category).where(Document.project_id == pid).distinct()))
        if stage == "proposal" and "proposal_report" not in cats:
            issues.append("Upload the proposal report")
        if stage == "scrutiny":
            have = len(cats & set(REQUIRED_DOC_CATEGORIES))
            if have < 4:
                missing = sorted(set(REQUIRED_DOC_CATEGORIES) - cats)
                issues.append(f"At least 4 of 5 required documents needed; missing: {', '.join(missing)}")
    elif stage == "notification":
        if not db.scalar(select(func.count()).select_from(Notification).where(Notification.project_id == pid)):
            issues.append("Record at least one land notification")
    elif stage == "award":
        if not db.scalar(select(func.count()).select_from(Award).where(Award.project_id == pid)):
            issues.append("Declare at least one award")
    elif stage == "compensation":
        assessed, disbursed = db.execute(
            select(func.coalesce(func.sum(Compensation.assessed_cr), 0.0), func.coalesce(func.sum(Compensation.disbursed_cr), 0.0)).where(
                Compensation.project_id == pid
            )
        ).one()
        if assessed <= 0:
            issues.append("Assess compensation for the parcels")
        elif 100.0 * disbursed / assessed < COMPENSATION_GATE_PCT:
            issues.append(
                f"Compensation disbursed is {100.0 * disbursed / assessed:.0f}% of assessed; "
                f"{COMPENSATION_GATE_PCT:.0f}% needed"
            )
    elif stage == "possession":
        pending = db.scalar(
            select(func.count()).select_from(Parcel).where(Parcel.project_id == pid, Parcel.status != "possessed")
        )
        if pending:
            issues.append(f"{pending} parcel(s) not yet possessed")
    elif stage == "rr":
        open_families = db.scalar(
            select(func.count())
            .select_from(Family)
            .where(Family.project_id == pid, Family.displaced.is_(True), Family.rr_status != "resettled")
        )
        if open_families:
            issues.append(f"{open_families} displaced family(ies) not yet resettled")
    return issues


def advance(db: Session, project: Project, user: User) -> Project:
    keys = plan_keys(project.template_key)
    if project.stage not in keys:
        raise WorkflowError(f"Stage '{project.stage}' is not part of this project's template", status_code=422)
    idx = keys.index(project.stage)
    if idx == len(keys) - 1:
        raise WorkflowError("Project is already at its final stage")
    if user.role not in ADVANCE_ROLES.get(project.stage, set()):
        raise WorkflowError(
            f"Role '{user.role}' cannot complete the '{STAGE_LABELS[project.stage]}' stage", status_code=403
        )
    unmet = gate_issues(db, project)
    if unmet:
        raise WorkflowError("Stage gate not satisfied", unmet)

    now = utcnow()
    old, new = project.stage, keys[idx + 1]
    old_ms = _milestone(db, project.id, old)
    new_ms = _milestone(db, project.id, new)
    started = (old_ms.actual_start if old_ms and old_ms.actual_start else project.stage_entered_at)
    if old_ms:
        old_ms.actual_start = started
        old_ms.actual_end = now
    if new_ms:
        new_ms.actual_start = now

    # Parcel status follows project-level milestones. Compensation and possession are parcel-level.
    if old == "notification":
        for p in db.scalars(select(Parcel).where(Parcel.project_id == project.id, Parcel.status == "proposed")):
            p.status = "notified"
    if old == "award":
        for p in db.scalars(select(Parcel).where(Parcel.project_id == project.id, Parcel.status == "notified")):
            p.status = "awarded"

    # Label this stage's earlier risk snapshots with the real outcome (feeds retraining).
    actual_days = (now - started).total_seconds() / 86400
    planned = max(1, old_ms.planned_days if old_ms else 1)
    delayed = 1 if actual_days > DELAY_TOLERANCE * planned else 0
    for rs in db.scalars(
        select(RiskScore).where(RiskScore.project_id == project.id, RiskScore.stage == old, RiskScore.label.is_(None))
    ):
        rs.label, rs.labelled_at = delayed, now

    project.stage = new
    project.stage_entered_at = now
    if idx + 1 == len(keys) - 1:
        project.status = "completed"
        if new_ms:
            new_ms.actual_end = now

    detail = f"{STAGE_LABELS[old]} -> {STAGE_LABELS[new]}"
    db.add(Event(project_id=project.id, kind="stage_change", stage=new, detail=detail, actor=user.username, at=now))
    audit.log(
        db, user, "stage_advance", "project", project.id,
        {"from": old, "to": new, "actual_days": round(actual_days, 1), "planned_days": planned, "delayed": bool(delayed)},
    )
    db.flush()
    return project


def stall(db: Session, project: Project, user: User, reason: str, note: str = "") -> Project:
    if reason not in STALL_REASONS:
        raise WorkflowError("Unknown stall reason", status_code=422)
    if project.status != "active":
        raise WorkflowError("Only active projects can be stalled")
    now = utcnow()
    project.status, project.stall_reason, project.stall_note, project.stalled_since = "stalled", reason, note, now
    db.add(
        Event(project_id=project.id, kind="stalled", stage=project.stage, actor=user.username, at=now,
              detail=f"{STALL_REASONS[reason]}. {note}".strip())
    )
    audit.log(db, user, "project_stalled", "project", project.id, {"reason": reason, "note": note})
    db.flush()
    return project


def resume(db: Session, project: Project, user: User, note: str = "") -> Project:
    if project.status != "stalled":
        raise WorkflowError("Project is not stalled")
    now = utcnow()
    prev = project.stall_reason
    project.status, project.stall_reason, project.stall_note, project.stalled_since = "active", None, None, None
    db.add(Event(project_id=project.id, kind="resumed", stage=project.stage, actor=user.username, at=now,
                 detail=f"Resumed after: {STALL_REASONS.get(prev, prev)}. {note}".strip()))
    audit.log(db, user, "project_resumed", "project", project.id, {"previous_reason": prev, "note": note})
    db.flush()
    return project


def stage_view(db: Session, project: Project) -> list[dict]:
    """Per-stage status for the project detail screen."""
    keys = plan_keys(project.template_key)
    cur = keys.index(project.stage) if project.stage in keys else -1
    out = []
    for ms in project.milestones:
        if ms.stage not in keys:
            continue
        i = keys.index(ms.stage)
        state = "done" if (i < cur or project.status == "completed") else ("current" if i == cur else "upcoming")
        actual_days = None
        if ms.actual_start and ms.actual_end:
            actual_days = round((ms.actual_end - ms.actual_start).total_seconds() / 86400, 1)
        out.append(
            {
                "stage": ms.stage,
                "label": STAGE_LABELS[ms.stage],
                "state": state,
                "planned_days": ms.planned_days,
                "planned_end": ms.planned_end.isoformat(),
                "actual_days": actual_days,
                "delayed": bool(actual_days is not None and actual_days > DELAY_TOLERANCE * max(1, ms.planned_days)),
            }
        )
    out.sort(key=lambda s: keys.index(s["stage"]))
    return out
