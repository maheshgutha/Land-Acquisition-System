"""Response shaping shared by several routers."""
from ..models import Project, RiskScore
from ..workflow import DELAY_TOLERANCE, STAGE_LABELS


def risk_dict(rs: RiskScore | None, full: bool = False) -> dict | None:
    if rs is None:
        return None
    d = {
        "score": rs.score,
        "probability": rs.probability,
        "category": rs.category,
        "stage": rs.stage,
        "scored_at": rs.scored_at.isoformat(),
        "model_version": rs.model_version,
        # Past 125% of plan the stage counts as delayed by definition, so the probability saturates near 100%.
        "already_delayed": bool((rs.features or {}).get("overrun_ratio", 0) > DELAY_TOLERANCE),
    }
    if full:
        d.update(drivers=rs.drivers, recommendations=rs.recommendations, stage_profile=rs.stage_profile)
    return d


def project_row(p: Project, m: dict) -> dict:
    rs = m["risk"]
    return {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "project_type": p.project_type,
        "agency": p.agency,
        "state": p.state,
        "district": p.district,
        "stage": p.stage,
        "stage_label": STAGE_LABELS.get(p.stage, p.stage),
        "status": p.status,
        "stall_reason": p.stall_reason,
        "progress_pct": m["progress_pct"],
        "days_in_stage": m["days_in_stage"],
        "planned_days": m["planned_days"],
        "overdue": bool(p.status == "active" and m["days_in_stage"] > max(1, m["planned_days"])),
        "proposed_area_ha": round(p.proposed_area_ha, 2),
        "area_notified_ha": round(m["area_notified"], 2),
        "area_acquired_ha": round(m["area_acquired"], 2),
        "comp_assessed_cr": round(m["assessed"], 2),
        "comp_disbursed_cr": round(m["disbursed"], 2),
        "comp_pct": m["comp_pct"],
        "families": m["families"],
        "displaced": m["displaced"],
        "rr_progress_pct": m["rr_progress_pct"],
        "legal_disputes": p.legal_disputes,
        "risk_score": rs.score if rs else None,
        "risk_category": rs.category if rs else None,
        "overrun_ratio": round(m["days_in_stage"] / max(1, m["planned_days"]), 2) if m["planned_days"] else 0.0,
        "already_delayed": bool(
            p.status != "completed" and m["planned_days"] and m["days_in_stage"] > DELAY_TOLERANCE * max(1, m["planned_days"])
        ),
    }
