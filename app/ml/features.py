"""Feature definitions shared by training, scoring and the synthetic data generator."""
from ..workflow import STAGE_KEYS

PROJECT_TYPES = ["highway", "railway", "solar_park", "irrigation", "urban", "industrial_corridor"]
STATES = [
    "Andhra Pradesh", "Telangana", "Karnataka", "Maharashtra", "Tamil Nadu", "Kerala", "Odisha",
    "West Bengal", "Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Bihar", "Gujarat",
]

FEATURES = [
    "project_type_code",
    "state_code",
    "area_ha",
    "families_affected",
    "displaced_ratio",
    "est_cost_cr",
    "stage_idx",
    "days_in_stage",
    "planned_days",
    "overrun_ratio",
    "approvals_pending",
    "legal_disputes",
    "ownership_conflicts",
    "comp_disbursed_pct",
    "rr_progress_pct",
    "doc_completeness",
    "avg_response_days",
    "agency_delay_rate",
    "district_delay_rate",
]

PREDICTABLE_STAGES = [k for k in STAGE_KEYS if k != "closeout"]


def code_of(vocab: list[str], value: str) -> int:
    return vocab.index(value) if value in vocab else -1


def feature_row(project, m: dict, rates: dict) -> dict:
    """One project's model input, from its aggregated metrics `m` (see services.metrics)."""
    planned = max(1, m["planned_days"])
    days = m["days_in_stage"]
    fam, disp = m["families"], m["displaced"]
    return {
        "project_type_code": code_of(PROJECT_TYPES, project.project_type),
        "state_code": code_of(STATES, project.state),
        "area_ha": round(project.proposed_area_ha, 2),
        "families_affected": fam,
        "displaced_ratio": round(disp / fam, 3) if fam else 0.0,
        "est_cost_cr": round(project.estimated_cost_cr, 2),
        "stage_idx": STAGE_KEYS.index(project.stage),
        "days_in_stage": days,
        "planned_days": planned,
        "overrun_ratio": round(days / planned, 3),
        "approvals_pending": project.approvals_pending,
        "legal_disputes": project.legal_disputes,
        "ownership_conflicts": m["disputed_parcels"],
        "comp_disbursed_pct": m["comp_pct"],
        "rr_progress_pct": m["rr_progress_pct"],
        "doc_completeness": m["doc_completeness"],
        "avg_response_days": round(project.avg_response_days, 1),
        "agency_delay_rate": rates["agency"].get(project.agency, rates["global"]),
        "district_delay_rate": rates["district"].get(project.district, rates["global"]),
    }
