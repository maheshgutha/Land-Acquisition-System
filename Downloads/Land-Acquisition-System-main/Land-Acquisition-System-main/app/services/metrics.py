"""Aggregate per-project numbers once, so lists, dashboards, reports and the model share them."""
from collections import defaultdict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Compensation, Document, Family, Milestone, Parcel, Project, RiskScore
from ..workflow import DELAY_TOLERANCE, REQUIRED_DOC_CATEGORIES, RR_WEIGHTS, days_in_stage, progress_pct

NOTIFIED = {"notified", "awarded", "compensated", "possessed"}
AWARDED = {"awarded", "compensated", "possessed"}


def build_metrics(db: Session, projects: list[Project]) -> dict[int, dict]:
    ids = [p.id for p in projects]
    if not ids:
        return {}
    m: dict[int, dict] = {}
    for p in projects:
        m[p.id] = dict(
            parcels=0, area_total=0.0, area_notified=0.0, area_awarded=0.0, area_acquired=0.0,
            disputed_parcels=0, assessed=0.0, disbursed=0.0, families=0, displaced=0,
            rr_status_counts=defaultdict(int), doc_cats=set(),
        )

    parcel_rows = db.execute(
        select(
            Parcel.project_id, Parcel.status, func.count(), func.coalesce(func.sum(Parcel.area_ha), 0.0),
            func.coalesce(func.sum(case((Parcel.disputed.is_(True), 1), else_=0)), 0),
        )
        .where(Parcel.project_id.in_(ids))
        .group_by(Parcel.project_id, Parcel.status)
    ).all()
    for pid, status, n, area, disputed in parcel_rows:
        x = m[pid]
        x["parcels"] += n
        x["area_total"] += area
        x["disputed_parcels"] += disputed
        if status in NOTIFIED:
            x["area_notified"] += area
        if status in AWARDED:
            x["area_awarded"] += area
        if status == "possessed":
            x["area_acquired"] += area

    for pid, assessed, disbursed in db.execute(
        select(
            Compensation.project_id,
            func.coalesce(func.sum(Compensation.assessed_cr), 0.0),
            func.coalesce(func.sum(Compensation.disbursed_cr), 0.0),
        )
        .where(Compensation.project_id.in_(ids))
        .group_by(Compensation.project_id)
    ):
        m[pid]["assessed"], m[pid]["disbursed"] = assessed, disbursed

    for pid, displaced, rr_status, n in db.execute(
        select(Family.project_id, Family.displaced, Family.rr_status, func.count())
        .where(Family.project_id.in_(ids))
        .group_by(Family.project_id, Family.displaced, Family.rr_status)
    ):
        x = m[pid]
        x["families"] += n
        if displaced:
            x["displaced"] += n
            x["rr_status_counts"][rr_status] += n

    for pid, cat in db.execute(
        select(Document.project_id, Document.category).where(Document.project_id.in_(ids)).distinct()
    ):
        m[pid]["doc_cats"].add(cat)

    planned = {(pid, stage): d for pid, stage, d in db.execute(
        select(Milestone.project_id, Milestone.stage, Milestone.planned_days).where(Milestone.project_id.in_(ids))
    )}

    latest_ids = select(func.max(RiskScore.id)).where(RiskScore.project_id.in_(ids)).group_by(RiskScore.project_id)
    latest = {rs.project_id: rs for rs in db.scalars(select(RiskScore).where(RiskScore.id.in_(latest_ids)))}

    for p in projects:
        x = m[p.id]
        x["comp_pct"] = round(100.0 * x["disbursed"] / x["assessed"], 1) if x["assessed"] > 0 else 0.0
        if x["displaced"] == 0:
            x["rr_progress_pct"] = 100.0
        else:
            total = sum(RR_WEIGHTS.get(s, 0.0) * n for s, n in x["rr_status_counts"].items())
            x["rr_progress_pct"] = round(100.0 * total / x["displaced"], 1)
        x["doc_completeness"] = round(100.0 * len(x["doc_cats"] & set(REQUIRED_DOC_CATEGORIES)) / len(REQUIRED_DOC_CATEGORIES), 1)
        x["planned_days"] = planned.get((p.id, p.stage), 0)
        x["days_in_stage"] = round(days_in_stage(p), 1) if p.status != "completed" else 0.0
        x["progress_pct"] = progress_pct(p)
        x["risk"] = latest.get(p.id)
    return m


def historic_delay_rates(db: Session) -> dict:
    """Share of completed stages that ran >25% over plan, by agency and by district.

    Shrunk toward the global rate so a single stage does not swing a small agency to 0% or 100%.
    """
    rows = db.execute(
        select(Project.agency, Project.district, Milestone.planned_days, Milestone.actual_start, Milestone.actual_end)
        .join(Milestone, Milestone.project_id == Project.id)
        .where(Milestone.actual_end.is_not(None), Milestone.actual_start.is_not(None), Milestone.planned_days > 0)
    ).all()
    agency, district = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    tot = delayed_tot = 0
    for ag, di, planned, start, end in rows:
        late = (end - start).total_seconds() / 86400 > DELAY_TOLERANCE * planned
        tot += 1
        delayed_tot += late
        for bucket, key in ((agency, ag), (district, di)):
            bucket[key][0] += 1
            bucket[key][1] += late
    glob = (delayed_tot / tot) if tot else 0.3
    k = 3.0
    shrink = lambda b: {key: round((d + glob * k) / (n + k), 3) for key, (n, d) in b.items()}
    return {"agency": shrink(agency), "district": shrink(district), "global": round(glob, 3)}
