"""Turns SHAP contributions into readable drivers and playbook recommendations.

Recommendations are deterministic: a driver only produces advice when SHAP says it is
pushing risk up AND the underlying value is actually bad. Nothing is generated freely.
"""
from ..workflow import STAGE_KEYS, STAGE_LABELS
from .features import FEATURES

FEATURE_LABELS = {
    "project_type_code": "Project type",
    "state_code": "State",
    "area_ha": "Land area (ha)",
    "families_affected": "Affected families",
    "displaced_ratio": "Share of families displaced",
    "est_cost_cr": "Estimated cost (Rs crore)",
    "stage_idx": "Lifecycle stage",
    "days_in_stage": "Days in current stage",
    "planned_days": "Planned days for stage",
    "overrun_ratio": "Stage time used vs plan",
    "approvals_pending": "Pending approvals",
    "legal_disputes": "Open legal disputes",
    "ownership_conflicts": "Parcels with ownership conflicts",
    "comp_disbursed_pct": "Compensation disbursed (%)",
    "rr_progress_pct": "R&R progress (%)",
    "doc_completeness": "Document completeness (%)",
    "avg_response_days": "Avg stakeholder response (days)",
    "agency_delay_rate": "Agency's past delay rate",
    "district_delay_rate": "District's past delay rate",
}


def _pct(v):
    return f"{v:.0f}%"


def build_drivers(contribs, row: dict, top: int = 6) -> list[dict]:
    """Top contributions by size. `impact` is SHAP in log-odds; the sign is the direction."""
    pairs = sorted(zip(FEATURES, contribs), key=lambda kv: abs(kv[1]), reverse=True)[:top]
    out = []
    for feat, c in pairs:
        out.append(
            {
                "feature": feat,
                "label": FEATURE_LABELS.get(feat, feat),
                "value": row.get(feat),
                "impact": round(float(c), 3),
                "direction": "increases" if c > 0 else "reduces",
            }
        )
    return out


def _rules(row: dict, stage_label: str):
    """feature -> (is_bad, action text, suggested owner)."""
    stage_idx = int(row["stage_idx"])
    displaced = round(row["families_affected"] * row["displaced_ratio"])
    return {
        "overrun_ratio": (
            row["overrun_ratio"] > 1.0,
            f"The '{stage_label}' stage has used {row['days_in_stage']:.0f} of {row['planned_days']:.0f} planned days. "
            "Escalate to the District Collector and set a 7-day review checkpoint.",
            "District",
        ),
        "days_in_stage": (
            row["overrun_ratio"] > 1.0,
            f"The '{stage_label}' stage has used {row['days_in_stage']:.0f} of {row['planned_days']:.0f} planned days. "
            "Escalate to the District Collector and set a 7-day review checkpoint.",
            "District",
        ),
        "legal_disputes": (
            row["legal_disputes"] >= 1,
            f"Assign the legal cell to the {int(row['legal_disputes'])} open dispute(s); seek early hearings or mediation.",
            "State legal cell",
        ),
        "ownership_conflicts": (
            row["ownership_conflicts"] >= 1,
            f"Resolve title conflicts on {int(row['ownership_conflicts'])} parcel(s) with land-record verification before the award.",
            "District",
        ),
        "approvals_pending": (
            row["approvals_pending"] >= 1,
            f"Clear {int(row['approvals_pending'])} pending inter-department approval(s) in one joint review meeting.",
            "State",
        ),
        "comp_disbursed_pct": (
            stage_idx >= 6 and row["comp_disbursed_pct"] < 70,
            f"Release the remaining {100 - row['comp_disbursed_pct']:.0f}% of compensation in tranches and unblock the sanction queue.",
            "District finance",
        ),
        "rr_progress_pct": (
            stage_idx >= 7 and row["rr_progress_pct"] < 60 and displaced > 0,
            f"Fast-track R&R packages: about {displaced} displaced families are affected and progress is {_pct(row['rr_progress_pct'])}.",
            "R&R officer",
        ),
        "displaced_ratio": (
            stage_idx >= 6 and row["rr_progress_pct"] < 60 and displaced > 0,
            f"Start R&R consultations early: about {displaced} families are displaced.",
            "R&R officer",
        ),
        "doc_completeness": (
            stage_idx <= 2 and row["doc_completeness"] < 100,
            f"Collect the missing documents (completeness {_pct(row['doc_completeness'])}); use the required-document checklist.",
            "Agency",
        ),
        "avg_response_days": (
            row["avg_response_days"] > 7,
            f"Stakeholder responses average {row['avg_response_days']:.0f} days. Set a 48-hour response target with weekly follow-ups.",
            "Nodal officer",
        ),
        "agency_delay_rate": (
            row["agency_delay_rate"] > 0.4,
            f"This agency's past stages ran late {row['agency_delay_rate']:.0%} of the time. Assign a dedicated nodal officer.",
            "Agency",
        ),
        "district_delay_rate": (
            row["district_delay_rate"] > 0.4,
            f"Stages in this district ran late {row['district_delay_rate']:.0%} of the time. Review district staffing and case load.",
            "State",
        ),
    }


def build_recommendations(drivers: list[dict], row: dict, max_items: int = 4) -> list[dict]:
    stage_label = STAGE_LABELS[STAGE_KEYS[int(row["stage_idx"])]]
    rules = _rules(row, stage_label)
    out, seen = [], set()
    for d in drivers:
        if d["direction"] != "increases" or d["feature"] not in rules:
            continue
        bad, action, owner = rules[d["feature"]]
        if not bad or action in seen:
            continue
        seen.add(action)
        out.append({"driver": d["label"], "action": action, "owner": owner, "impact": d["impact"]})
        if len(out) == max_items:
            break
    return out
