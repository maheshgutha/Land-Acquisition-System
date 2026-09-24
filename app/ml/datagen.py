"""Synthetic training data for the delay-risk model.

IMPORTANT: there is no public labelled history of land-acquisition stage delays, so the
labels below come from a hand-written causal model. The effect sizes are ASSUMPTIONS
based on the delay causes named in the problem statement (approvals, disputes,
compensation, R&R, documentation, responsiveness). A model trained on this data learns
those assumptions, so any accuracy it reports is not evidence about real projects.
Replace this with real project history (via the retraining endpoint) before relying on it.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..workflow import DELAY_TOLERANCE, DEFAULT_DAYS, STAGE_KEYS
from .features import FEATURES, PROJECT_TYPES, STATES

_STAGE_WEIGHTS = [0.08, 0.12, 0.12, 0.10, 0.16, 0.10, 0.14, 0.08, 0.10]  # closeout excluded
_STAGE_BUMP = {4: 0.30, 5: 0.15, 6: 0.40, 8: 0.50}


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def generate(n: int = 8000, seed: int = 42, today: date | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    today = today or date.today()

    stage_idx = rng.choice(9, size=n, p=_STAGE_WEIGHTS)
    base_days = np.array([DEFAULT_DAYS[STAGE_KEYS[i]] for i in stage_idx], dtype=float)
    planned = np.maximum(1, np.round(base_days * rng.choice([0.75, 1.0, 1.0, 1.5], size=n))).astype(int)
    frac = rng.uniform(0.02, 1.4, size=n)  # how far into the stage the snapshot was taken
    days_in_stage = np.round(planned * frac, 1)
    overrun = np.round(days_in_stage / planned, 3)

    area = np.clip(rng.lognormal(np.log(30), 0.9, n), 2, 400).round(2)
    families = rng.poisson(area * 0.55)
    displaced_ratio = np.where(families > 0, rng.uniform(0.1, 0.6, n), 0.0).round(3)
    cost = (area * rng.uniform(0.8, 3.0, n)).round(2)

    early = stage_idx <= 3
    approvals = np.minimum(5, np.where(early, rng.poisson(1.0, n), rng.poisson(0.3, n)))
    disputes = np.minimum(6, rng.poisson(0.8, n))
    conflicts = np.minimum(8, rng.poisson(area / 40.0))
    response = np.clip(rng.gamma(4.0, 1.5, n), 1, 25).round(1)
    agency_rate = np.clip(rng.beta(3, 6, n) + rng.normal(0, 0.03, n), 0.02, 0.95).round(3)
    district_rate = np.clip(rng.beta(3, 6, n) + rng.normal(0, 0.03, n), 0.02, 0.95).round(3)

    doc = np.where(
        stage_idx == 0,
        rng.choice([0, 20, 40, 60, 80, 100], n),
        np.where(stage_idx == 1, rng.choice([40, 60, 80, 100], n), rng.choice([80, 100], n, p=[0.2, 0.8])),
    ).astype(float)
    comp = np.zeros(n)
    comp = np.where(stage_idx == 6, rng.uniform(0, 100, n), comp)
    comp = np.where(stage_idx >= 7, np.where(rng.random(n) < 0.7, 100.0, rng.uniform(95, 100, n)), comp).round(1)
    rr = np.where(stage_idx == 7, rng.uniform(0, 25, n), np.where(stage_idx == 8, rng.uniform(0, 100, n), 0.0))
    rr = np.where(families * displaced_ratio < 1, 100.0, rr).round(1)

    ptype = rng.integers(0, len(PROJECT_TYPES), n)
    # Assumed type effects: solar parks are simpler (mostly barren or single-owner land); urban is harder.
    type_effect = np.array([0.10, 0.20, -0.25, 0.05, 0.30, 0.15])[ptype]

    z = (
        -2.65
        + type_effect
        + 1.1 * np.maximum(overrun - 0.8, 0)
        + 0.42 * disputes
        + 0.15 * conflicts
        + 0.33 * approvals * early
        + 0.05 * (response - 6)
        + 2.0 * (agency_rate - 0.33)
        + 1.6 * (district_rate - 0.33)
        + 0.012 * (100 - doc) * (stage_idx <= 2)
        + 0.020 * (100 - comp) * (stage_idx == 6)
        + 0.010 * (100 - rr) * (stage_idx >= 7) * (displaced_ratio > 0)
        + 0.8 * displaced_ratio * (stage_idx >= 6)
        + 0.25 * np.log1p(area / 50.0)
        + 0.30 * disputes * np.isin(stage_idx, [4, 5]) * 0.5
        + np.array([_STAGE_BUMP.get(int(i), 0.0) for i in stage_idx])
        + rng.normal(0, 0.6, n)
    )
    label = (rng.random(n) < _sigmoid(z)).astype(int)
    label = np.where(overrun > DELAY_TOLERANCE, 1, label)  # already past the delay line

    df = pd.DataFrame(
        {
            "project_type_code": ptype,
            "state_code": rng.integers(0, len(STATES), n),
            "area_ha": area,
            "families_affected": families,
            "displaced_ratio": displaced_ratio,
            "est_cost_cr": cost,
            "stage_idx": stage_idx,
            "days_in_stage": days_in_stage,
            "planned_days": planned,
            "overrun_ratio": overrun,
            "approvals_pending": approvals,
            "legal_disputes": disputes,
            "ownership_conflicts": conflicts,
            "comp_disbursed_pct": comp,
            "rr_progress_pct": rr,
            "doc_completeness": doc,
            "avg_response_days": response,
            "agency_delay_rate": agency_rate,
            "district_delay_rate": district_rate,
        }
    )[FEATURES]
    df["label"] = label
    offsets = rng.integers(0, 1095, n)
    df["snapshot_date"] = [pd.Timestamp(today - timedelta(days=int(d))) for d in offsets]
    df["is_real"] = 0
    return df
