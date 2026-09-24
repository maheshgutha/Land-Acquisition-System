"""Dashboard KPIs, trends, GIS layers, MIS reports and alerts."""
import csv
import io
from collections import defaultdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, geo
from ..db import get_db
from ..models import Alert, Award, Milestone, Notification, Parcel, Project, RiskScore, User
from ..security import can_see_owner_names, get_current_user, project_scope, require_roles, scoped_projects
from ..services.metrics import build_metrics
from ..services.serialize import project_row
from ..workflow import DELAY_TOLERANCE, RR_WEIGHTS, STAGE_KEYS, STAGE_LABELS

router = APIRouter(prefix="/api", tags=["analytics"])

RISK_ORDER = ["low", "medium", "high", "critical"]


def _projects(db: Session, user: User, state: Optional[str] = None, district: Optional[str] = None) -> list[Project]:
    q = scoped_projects(user)
    if state:
        q = q.where(Project.state == state)
    if district:
        q = q.where(Project.district == district)
    return list(db.scalars(q))


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard/summary")
def summary(
    state: Optional[str] = None, district: Optional[str] = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    projects = _projects(db, user, state, district)
    metrics = build_metrics(db, projects)
    ids = [p.id for p in projects]
    ms = list(metrics.values())

    def total(key):
        return sum(x[key] for x in ms)

    by_stage = defaultdict(int)
    for p in projects:
        by_stage[p.stage] += 1
    rr_counts: dict[str, int] = defaultdict(int)
    for x in ms:
        for k, n in x["rr_status_counts"].items():
            rr_counts[k] += n
    displaced = total("displaced")
    rr_weighted = sum(RR_WEIGHTS.get(k, 0) * n for k, n in rr_counts.items())
    parcels_total = total("parcels")
    parcels_possessed = 0
    if ids:
        parcels_possessed = db.scalar(
            select(func.count()).select_from(Parcel).where(Parcel.project_id.in_(ids), Parcel.status == "possessed")
        )
    n_notifications = n_awards = 0
    award_amount = 0.0
    on_time = done = 0
    if ids:
        n_notifications = db.scalar(select(func.count()).select_from(Notification).where(Notification.project_id.in_(ids)))
        n_awards, award_amount = db.execute(
            select(func.count(), func.coalesce(func.sum(Award.total_amount_cr), 0.0)).where(Award.project_id.in_(ids))
        ).one()
        for planned, start, end in db.execute(
            select(Milestone.planned_days, Milestone.actual_start, Milestone.actual_end)
            .where(Milestone.project_id.in_(ids), Milestone.actual_end.is_not(None), Milestone.actual_start.is_not(None), Milestone.planned_days > 0)
        ):
            done += 1
            on_time += (end - start).total_seconds() / 86400 <= DELAY_TOLERANCE * planned

    active = [p for p in projects if p.status == "active"]
    overdue = sum(1 for p in active if metrics[p.id]["days_in_stage"] > max(1, metrics[p.id]["planned_days"]))
    risk = defaultdict(int)
    for p in projects:
        if p.status == "completed":
            continue
        rs = metrics[p.id]["risk"]
        risk[rs.category if rs else "unscored"] += 1
    assessed, disbursed = total("assessed"), total("disbursed")

    return {
        "scope": {"state": state, "district": district},
        "projects": {
            "total": len(projects),
            "active": len(active),
            "stalled": sum(1 for p in projects if p.status == "stalled"),
            "completed": sum(1 for p in projects if p.status == "completed"),
        },
        "by_stage": [{"stage": k, "label": STAGE_LABELS[k], "count": by_stage.get(k, 0)} for k in STAGE_KEYS],
        "area_ha": {
            "proposed": round(sum(p.proposed_area_ha for p in projects), 1),
            "notified": round(total("area_notified"), 1),
            "awarded": round(total("area_awarded"), 1),
            "acquired": round(total("area_acquired"), 1),
        },
        "notifications": n_notifications,
        "awards": {"count": n_awards, "amount_cr": round(award_amount, 2)},
        "compensation": {
            "assessed_cr": round(assessed, 2),
            "disbursed_cr": round(disbursed, 2),
            "disbursed_pct": round(100 * disbursed / assessed, 1) if assessed else 0.0,
        },
        "families": {
            "affected": total("families"),
            "displaced": displaced,
            "rr_status": {k: rr_counts.get(k, 0) for k in RR_WEIGHTS},
            "rr_progress_pct": round(100 * rr_weighted / displaced, 1) if displaced else 0.0,
        },
        "possession": {
            "parcels_total": parcels_total,
            "parcels_possessed": parcels_possessed,
            "pct": round(100 * parcels_possessed / parcels_total, 1) if parcels_total else 0.0,
        },
        "timeline": {
            "active_projects_on_schedule_pct": round(100 * (len(active) - overdue) / len(active), 1) if active else 100.0,
            "overdue_projects": overdue,
            "completed_stages_on_time_pct": round(100 * on_time / done, 1) if done else None,
        },
        "risk": {k: risk.get(k, 0) for k in [*RISK_ORDER, "unscored"]},
    }


@router.get("/dashboard/by-state")
def by_state(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = _projects(db, user)
    metrics = build_metrics(db, projects)
    groups: dict[str, dict] = {}
    for p in projects:
        m = metrics[p.id]
        g = groups.setdefault(p.state, dict(state=p.state, projects=0, progress_sum=0.0, proposed_ha=0.0, acquired_ha=0.0,
                                            assessed_cr=0.0, disbursed_cr=0.0, displaced=0, high_risk=0, overdue=0, stalled=0))
        g["projects"] += 1
        g["progress_sum"] += m["progress_pct"]
        g["proposed_ha"] += p.proposed_area_ha
        g["acquired_ha"] += m["area_acquired"]
        g["assessed_cr"] += m["assessed"]
        g["disbursed_cr"] += m["disbursed"]
        g["displaced"] += m["displaced"]
        g["high_risk"] += bool(m["risk"] and m["risk"].category in ("high", "critical"))
        g["overdue"] += bool(p.status == "active" and m["days_in_stage"] > max(1, m["planned_days"]))
        g["stalled"] += p.status == "stalled"
    out = []
    for g in groups.values():
        n = g.pop("progress_sum")
        out.append({**g, "avg_progress_pct": round(n / g["projects"], 1),
                    "proposed_ha": round(g["proposed_ha"], 1), "acquired_ha": round(g["acquired_ha"], 1),
                    "assessed_cr": round(g["assessed_cr"], 2), "disbursed_cr": round(g["disbursed_cr"], 2)})
    return sorted(out, key=lambda g: g["state"])


@router.get("/dashboard/trends")
def trends(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delay trends across districts, project types and stages, plus risk over time."""
    projects = _projects(db, user)
    metrics = build_metrics(db, projects)
    ids = [p.id for p in projects]
    ptype = {p.id: p.project_type for p in projects}

    def group(keyfn):
        acc: dict[str, dict] = {}
        for p in projects:
            m = metrics[p.id]
            g = acc.setdefault(keyfn(p), dict(projects=0, risk_sum=0.0, scored=0, high_risk=0, overdue=0))
            g["projects"] += 1
            if m["risk"]:
                g["risk_sum"] += m["risk"].score
                g["scored"] += 1
                g["high_risk"] += m["risk"].category in ("high", "critical")
            g["overdue"] += bool(p.status == "active" and m["days_in_stage"] > max(1, m["planned_days"]))
        return [
            {"name": k, "projects": g["projects"], "avg_risk": round(g["risk_sum"] / g["scored"], 1) if g["scored"] else None,
             "high_risk": g["high_risk"], "overdue": g["overdue"]}
            for k, g in sorted(acc.items())
        ]

    stage_perf: dict[str, list] = defaultdict(list)
    delayed_by_type: dict[str, list] = defaultdict(list)
    if ids:
        for pid, stage, planned, start, end in db.execute(
            select(Milestone.project_id, Milestone.stage, Milestone.planned_days, Milestone.actual_start, Milestone.actual_end)
            .where(Milestone.project_id.in_(ids), Milestone.actual_end.is_not(None), Milestone.actual_start.is_not(None), Milestone.planned_days > 0)
        ):
            actual = (end - start).total_seconds() / 86400
            stage_perf[stage].append((planned, actual))
            delayed_by_type[ptype[pid]].append(actual > DELAY_TOLERANCE * planned)
    stage_rows = [
        {"stage": k, "label": STAGE_LABELS[k], "n": len(v),
         "avg_planned_days": round(sum(p for p, _ in v) / len(v), 1),
         "avg_actual_days": round(sum(a for _, a in v) / len(v), 1),
         "delayed_pct": round(100 * sum(a > DELAY_TOLERANCE * p for p, a in v) / len(v), 1)}
        for k in STAGE_KEYS if (v := stage_perf.get(k))
    ]
    type_rows = [{"name": t, "stages_completed": len(v), "delayed_pct": round(100 * sum(v) / len(v), 1)} for t, v in sorted(delayed_by_type.items())]

    monthly: dict[str, list] = defaultdict(list)
    if ids:
        for at, score in db.execute(select(RiskScore.scored_at, RiskScore.score).where(RiskScore.project_id.in_(ids))):
            monthly[at.strftime("%Y-%m")].append(score)
    return {
        "by_state": group(lambda p: p.state),
        "by_district": group(lambda p: f"{p.district}, {p.state}"),
        "by_project_type": group(lambda p: p.project_type),
        "stage_performance": stage_rows,
        "completed_stage_delay_by_type": type_rows,
        "risk_over_time": [{"month": k, "avg_score": round(sum(v) / len(v), 1), "n": len(v)} for k, v in sorted(monthly.items())],
    }


# ------------------------------------------------------------------ GIS
@router.get("/gis/parcels")
def parcels_geojson(
    project_id: Optional[int] = None, state: Optional[str] = None, district: Optional[str] = None,
    status: Optional[str] = None, bbox: Optional[str] = Query(default=None, description="minLon,minLat,maxLon,maxLat"),
    limit: int = Query(default=5000, le=20000),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    q = select(Parcel, Project.code, Project.name).join(Project, Project.id == Parcel.project_id)
    cond = project_scope(user)
    if cond is not None:
        q = q.where(cond)
    if project_id:
        q = q.where(Parcel.project_id == project_id)
    if state:
        q = q.where(Project.state == state)
    if district:
        q = q.where(Project.district == district)
    if status:
        q = q.where(Parcel.status == status)
    box = None
    if bbox:
        try:
            box = [float(x) for x in bbox.split(",")]
            assert len(box) == 4
        except (ValueError, AssertionError):
            raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat")
    show_owner = can_see_owner_names(user)
    features = []
    for parcel, code, pname in db.execute(q.limit(limit)):
        if not parcel.geometry:
            continue
        if box:
            c = geo.centroid(parcel.geometry)
            if not c or not (box[0] <= c[1] <= box[2] and box[1] <= c[0] <= box[3]):
                continue
        features.append({
            "type": "Feature",
            "geometry": parcel.geometry,
            "properties": {
                "id": parcel.id, "project_id": parcel.project_id, "project_code": code, "project_name": pname,
                "survey_no": parcel.survey_no, "village": parcel.village, "area_ha": round(parcel.area_ha, 2),
                "status": parcel.status, "disputed": parcel.disputed,
                "owner_name": parcel.owner_name if show_owner else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


@router.get("/gis/projects")
def projects_geojson(
    risk: Optional[str] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """One point per project (mean of its parcel centroids), for the high-risk map layer."""
    projects = _projects(db, user)
    metrics = build_metrics(db, projects)
    ids = [p.id for p in projects]
    pts: dict[int, list] = defaultdict(list)
    if ids:
        for pid, geometry in db.execute(select(Parcel.project_id, Parcel.geometry).where(Parcel.project_id.in_(ids))):
            c = geo.centroid(geometry)
            if c:
                pts[pid].append(c)
    features = []
    for p in projects:
        if not pts.get(p.id):
            continue
        row = project_row(p, metrics[p.id])
        if risk and row["risk_category"] != risk:
            continue
        lat = sum(c[0] for c in pts[p.id]) / len(pts[p.id])
        lon = sum(c[1] for c in pts[p.id]) / len(pts[p.id])
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                         "properties": {k: row[k] for k in ("id", "code", "name", "state", "district", "stage_label", "status",
                                                             "progress_pct", "days_in_stage", "planned_days", "risk_score", "risk_category",
                                                             "proposed_area_ha")}})
    return {"type": "FeatureCollection", "features": features}


# ------------------------------------------------------------------ MIS reports
PROJECT_COLUMNS = [
    "code", "name", "project_type", "agency", "state", "district", "stage_label", "status", "progress_pct",
    "days_in_stage", "planned_days", "proposed_area_ha", "area_acquired_ha", "comp_assessed_cr", "comp_disbursed_cr",
    "families", "displaced", "rr_progress_pct", "risk_score", "risk_category",
]
GROUP_KEYS = {"state": "state", "district": "district", "project_type": "project_type", "agency": "agency", "stage": "stage_label"}


@router.get("/reports/mis")
def mis_report(
    group_by: Optional[Literal["state", "district", "project_type", "agency", "stage"]] = None,
    columns: Optional[str] = Query(default=None, description="comma-separated project columns (row mode only)"),
    fmt: Literal["json", "csv"] = Query(default="json", alias="format"),
    state: Optional[str] = None,
    user: User = Depends(require_roles("central", "state", "district", "auditor", "agency")),
    db: Session = Depends(get_db),
):
    projects = _projects(db, user, state)
    metrics = build_metrics(db, projects)
    rows = [project_row(p, metrics[p.id]) for p in projects]

    if group_by:
        key = GROUP_KEYS[group_by]
        acc: dict[str, dict] = {}
        for r in rows:
            g = acc.setdefault(r[key], dict(group=r[key], projects=0, proposed_area_ha=0.0, area_acquired_ha=0.0,
                                            comp_assessed_cr=0.0, comp_disbursed_cr=0.0, families=0, displaced=0,
                                            risk_sum=0, scored=0, high_risk=0, overdue=0))
            g["projects"] += 1
            for k in ("proposed_area_ha", "area_acquired_ha", "comp_assessed_cr", "comp_disbursed_cr", "families", "displaced"):
                g[k] += r[k]
            if r["risk_score"] is not None:
                g["risk_sum"] += r["risk_score"]
                g["scored"] += 1
                g["high_risk"] += r["risk_category"] in ("high", "critical")
            g["overdue"] += r["overdue"]
        out, header = [], ["group", "projects", "proposed_area_ha", "area_acquired_ha", "comp_assessed_cr",
                           "comp_disbursed_cr", "families", "displaced", "avg_risk_score", "high_risk_projects", "overdue_projects"]
        for g in sorted(acc.values(), key=lambda x: str(x["group"])):
            out.append({"group": g["group"], "projects": g["projects"], "proposed_area_ha": round(g["proposed_area_ha"], 1),
                        "area_acquired_ha": round(g["area_acquired_ha"], 1), "comp_assessed_cr": round(g["comp_assessed_cr"], 2),
                        "comp_disbursed_cr": round(g["comp_disbursed_cr"], 2), "families": g["families"], "displaced": g["displaced"],
                        "avg_risk_score": round(g["risk_sum"] / g["scored"], 1) if g["scored"] else "",
                        "high_risk_projects": g["high_risk"], "overdue_projects": g["overdue"]})
    else:
        cols = [c.strip() for c in columns.split(",")] if columns else PROJECT_COLUMNS
        bad = [c for c in cols if c not in PROJECT_COLUMNS]
        if bad:
            raise HTTPException(422, f"Unknown column(s): {', '.join(bad)}. Allowed: {', '.join(PROJECT_COLUMNS)}")
        header, out = cols, [{c: r[c] for c in cols} for r in rows]

    audit.log(db, user, "report_exported", "report", "mis", {"group_by": group_by, "format": fmt, "rows": len(out)})
    db.commit()
    if fmt == "json":
        return {"columns": header, "rows": out}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header)
    w.writeheader()
    w.writerows(out)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="mis_report.csv"'})


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def list_alerts(
    include_acknowledged: bool = False, severity: Optional[str] = None, limit: int = Query(default=100, le=500),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    q = select(Alert, Project.code, Project.name).join(Project, Project.id == Alert.project_id)
    cond = project_scope(user)
    if cond is not None:
        q = q.where(cond)
    if not include_acknowledged:
        q = q.where(Alert.acknowledged.is_(False))
    if severity:
        q = q.where(Alert.severity == severity)
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = sorted(db.execute(q).all(), key=lambda r: (rank.get(r[0].severity, 9), -r[0].id))[:limit]
    return [
        {"id": a.id, "project_id": a.project_id, "project_code": code, "project_name": name, "kind": a.kind,
         "severity": a.severity, "message": a.message, "source": a.source, "created_at": a.created_at.isoformat(),
         "acknowledged": a.acknowledged}
        for a, code, name in rows
    ]


@router.post("/alerts/{alert_id}/ack")
def acknowledge_alert(
    alert_id: int, user: User = Depends(require_roles("central", "state", "district", "field", "agency")),
    db: Session = Depends(get_db),
):
    q = select(Alert).join(Project, Project.id == Alert.project_id).where(Alert.id == alert_id)
    cond = project_scope(user)
    if cond is not None:
        q = q.where(cond)
    alert = db.scalar(q)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.acknowledged, alert.acknowledged_by = True, user.username
    audit.log(db, user, "alert_acknowledged", "alert", alert.id, {"kind": alert.kind})
    db.commit()
    return {"id": alert.id, "acknowledged": True}
