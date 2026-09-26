"""Delay-risk endpoints: portfolio ranking, per-project explanation, rescoring, retraining."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..models import ModelVersion, Project, RiskScore, User
from ..security import get_current_user, get_project_or_404, require_roles, scoped_projects
from ..services import risk_service
from ..services.metrics import build_metrics
from ..services.serialize import project_row, risk_dict

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/portfolio")
def portfolio(
    state: Optional[str] = None, district: Optional[str] = None, category: Optional[str] = None,
    delayed: Optional[bool] = Query(default=None, description="true: only stages already past 125% of plan; false: only not-yet-late (early warnings)"),
    limit: int = Query(default=50, le=500),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Active projects ranked by delay risk: the intervention priority list."""
    q = scoped_projects(user).where(Project.status != "completed")
    if state:
        q = q.where(Project.state == state)
    if district:
        q = q.where(Project.district == district)
    projects = list(db.scalars(q))
    metrics = build_metrics(db, projects)
    rows = [project_row(p, metrics[p.id]) for p in projects]
    for r, p in zip(rows, projects):
        rs = metrics[p.id]["risk"]
        top = next((d for d in (rs.drivers if rs else []) if d["direction"] == "increases"), None)
        r["top_driver"] = top["label"] if top else None
    if category:
        rows = [r for r in rows if r["risk_category"] == category]
    if delayed is not None:
        rows = [r for r in rows if r["already_delayed"] == delayed]
    rows.sort(key=lambda r: (-(r["risk_score"] if r["risk_score"] is not None else -1), -r["overrun_ratio"]))
    return {"total": len(rows), "items": rows[:limit]}


@router.get("/projects/{project_id}")
def project_risk(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    latest = db.scalar(select(RiskScore).where(RiskScore.project_id == project.id).order_by(RiskScore.id.desc()).limit(1))
    if latest is None and project.status != "completed":
        scored = risk_service.score_projects(db, [project])
        latest = scored[0] if scored else None
        db.commit()
    history = db.execute(
        select(RiskScore.scored_at, RiskScore.score).where(RiskScore.project_id == project.id).order_by(RiskScore.id.desc()).limit(20)
    ).all()
    return {
        "project_id": project.id,
        "risk": risk_dict(latest, full=True),
        "history": [{"at": a.isoformat(), "score": s} for a, s in reversed(history)],
        "explanation_note": "Drivers are TreeSHAP contributions in log-odds: positive values raise delay risk, negative values lower it.",
    }


@router.post("/projects/{project_id}/rescore")
def rescore_project(
    project_id: int, user: User = Depends(require_roles("central", "state", "district")), db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    scored = risk_service.score_projects(db, [project])
    audit.log(db, user, "risk_rescored", "project", project.id, {"score": scored[0].score if scored else None})
    db.commit()
    return {"project_id": project.id, "risk": risk_dict(scored[0], full=True) if scored else None}


@router.post("/rescore")
def rescore_all(user: User = Depends(require_roles("central", "state")), db: Session = Depends(get_db)):
    projects = list(db.scalars(scoped_projects(user).where(Project.status != "completed")))
    scored = risk_service.score_projects(db, projects)
    audit.log(db, user, "risk_rescored_bulk", "project", "*", {"projects": len(scored)})
    db.commit()
    return {"rescored": len(scored)}


@router.post("/retrain")
def retrain(user: User = Depends(require_roles("central")), db: Session = Depends(get_db)):
    result = risk_service.retrain(db)
    audit.log(db, user, "model_retrain", "model", result.get("version", "-"),
              {"promoted": result["promoted"], "n_real": result.get("n_real"), "reason": result.get("reason")})
    db.commit()
    return result


@router.get("/model")
def model_info(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    risk_service.active_model(db)
    db.commit()
    versions = db.scalars(select(ModelVersion).order_by(ModelVersion.id.desc()).limit(10)).all()
    labelled = db.scalar(select(RiskScore.id).where(RiskScore.label.is_not(None)).limit(1)) is not None
    return {
        "training_data_note": (
            "The base model is trained on SYNTHETIC data built from assumed delay causes; its metrics do not "
            "measure accuracy on real projects. Recorded stage outcomes are added at retraining."
        ),
        "has_recorded_outcomes": labelled,
        "versions": [
            {"version": v.version, "active": v.active, "trained_at": v.trained_at.isoformat(), "n_train": v.n_train,
             "n_real": v.n_real, "metrics": v.metrics, "note": v.note}
            for v in versions
        ],
    }
