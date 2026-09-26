"""Scoring, alerts, model registry and retraining for the delay-risk model."""
from collections import defaultdict

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import MODEL_DIR
from ..ml import datagen
from ..ml import model as mlmodel
from ..ml.explain import build_drivers, build_recommendations
from ..ml.features import FEATURES, PREDICTABLE_STAGES, feature_row
from ..models import Alert, ModelVersion, Project, RiskScore, utcnow
from ..workflow import STAGE_KEYS, STAGE_LABELS, STALL_REASONS, plan
from .metrics import build_metrics, historic_delay_rates

BANDS = [(0.75, "critical"), (0.55, "high"), (0.30, "medium"), (0.0, "low")]
REAL_WEIGHT = 5.0
# Prototype threshold. In a real deployment require far more labelled outcomes (for example 200+).
MIN_REAL_ROWS = 1
_cache: dict = {"version": None, "bundle": None}


def categorize(p: float) -> str:
    for threshold, name in BANDS:
        if p >= threshold:
            return name
    return "low"


# ---------------------------------------------------------------- model registry
def _register(db: Session, bundle: dict, metrics: dict, n_real: int, note: str, activate: bool) -> ModelVersion:
    version = "v" + utcnow().strftime("%Y%m%d%H%M%S%f")
    mlmodel.save_bundle(bundle, MODEL_DIR / f"risk_{version}.joblib")
    if activate:
        for old in db.scalars(select(ModelVersion).where(ModelVersion.active.is_(True))):
            old.active = False
    mv = ModelVersion(version=version, metrics=metrics, n_train=metrics.get("n_train", 0), n_real=n_real, active=activate, note=note)
    db.add(mv)
    db.flush()
    if activate:
        _cache.update(version=version, bundle=bundle)
    return mv


def active_model(db: Session) -> tuple[dict, str]:
    mv = db.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)).order_by(ModelVersion.id.desc()))
    if mv:
        if _cache["version"] == mv.version:
            return _cache["bundle"], mv.version
        path = MODEL_DIR / f"risk_{mv.version}.joblib"
        if path.exists():
            bundle = mlmodel.load_bundle(path)
            _cache.update(version=mv.version, bundle=bundle)
            return bundle, mv.version
    bundle, metrics, _ = mlmodel.train_model(datagen.generate())
    mv = _register(db, bundle, metrics, 0, "Bootstrap: trained on synthetic data only", activate=True)
    return bundle, mv.version


# ---------------------------------------------------------------- alerts
def _upsert_alert(db: Session, project: Project, kind: str, severity: str, message: str, source: str = "rule") -> None:
    existing = db.scalar(
        select(Alert).where(Alert.project_id == project.id, Alert.kind == kind, Alert.acknowledged.is_(False))
    )
    if existing:
        existing.severity, existing.message = severity, message
        return
    db.add(Alert(project_id=project.id, kind=kind, severity=severity, message=message, source=source))


def raise_alerts(db: Session, project: Project, m: dict, rs: RiskScore) -> None:
    planned, days = max(1, m["planned_days"]), m["days_in_stage"]
    label = STAGE_LABELS.get(project.stage, project.stage)
    if project.status == "stalled":
        reason = STALL_REASONS.get(project.stall_reason or "", "reason not recorded")
        _upsert_alert(db, project, "stalled", "high", f"Project stalled at '{label}': {reason}.")
    if days > planned:
        sev = "high" if days > 1.5 * planned else "medium"
        _upsert_alert(db, project, "stage_overdue", sev, f"'{label}' has run {days:.0f} days against {planned} planned.")
    if project.stage == "compensation" and m["comp_pct"] < 40 and days > 0.5 * planned:
        _upsert_alert(db, project, "compensation_stalled", "high",
                      f"Only {m['comp_pct']:.0f}% of assessed compensation disbursed, {days:.0f} days into the stage.")
    if project.legal_disputes >= 2:
        _upsert_alert(db, project, "legal_disputes", "high" if project.legal_disputes >= 3 else "medium",
                      f"{project.legal_disputes} open legal disputes on this project.")
    if rs.category in ("high", "critical"):
        top = next((d for d in rs.drivers if d["direction"] == "increases"), None)
        why = f" Main driver: {top['label']} ({top['value']})." if top else ""
        _upsert_alert(db, project, "high_delay_risk", rs.category,
                      f"Delay probability {rs.score}% at '{label}'.{why}", source="model")


# ---------------------------------------------------------------- scoring
def score_projects(db: Session, projects: list[Project]) -> list[RiskScore]:
    projects = [p for p in projects if p.status != "completed" and p.stage in PREDICTABLE_STAGES]
    if not projects:
        return []
    bundle, version = active_model(db)
    metrics = build_metrics(db, projects)
    rates = historic_delay_rates(db)
    rows = [feature_row(p, metrics[p.id], rates) for p in projects]
    X = pd.DataFrame(rows)[FEATURES]
    probs = mlmodel.predict(bundle, X)
    contribs = mlmodel.contributions(bundle, X)

    # Stage-by-stage outlook: same project attributes, early-in-stage snapshot for each remaining stage.
    variants = []
    for i, (p, row) in enumerate(zip(projects, rows)):
        cur_idx = STAGE_KEYS.index(p.stage)
        for step in plan(p.template_key):
            k = step["key"]
            if k not in PREDICTABLE_STAGES or STAGE_KEYS.index(k) <= cur_idx:
                continue
            planned = max(1, step["days"])
            r = dict(row)
            r.update(stage_idx=STAGE_KEYS.index(k), planned_days=planned, days_in_stage=round(0.3 * planned, 1), overrun_ratio=0.3)
            variants.append((i, k, r))
    future = mlmodel.predict(bundle, pd.DataFrame([v[2] for v in variants])[FEATURES]) if variants else []

    profiles: dict[int, list] = defaultdict(list)
    for i, p in enumerate(projects):
        profiles[i].append({"stage": p.stage, "label": STAGE_LABELS[p.stage], "probability": round(float(probs[i]), 3), "current": True})
    for (i, k, _), pv in zip(variants, future):
        profiles[i].append({"stage": k, "label": STAGE_LABELS[k], "probability": round(float(pv), 3), "current": False})

    now, out = utcnow(), []
    for i, p in enumerate(projects):
        drivers = build_drivers(contribs[i], rows[i])
        prob = float(probs[i])
        rs = RiskScore(
            project_id=p.id, scored_at=now, stage=p.stage, probability=round(prob, 4), score=int(round(prob * 100)),
            category=categorize(prob), model_version=version, features=rows[i], drivers=drivers,
            recommendations=build_recommendations(drivers, rows[i]),
            stage_profile=sorted(profiles[i], key=lambda s: STAGE_KEYS.index(s["stage"])),
        )
        db.add(rs)
        out.append(rs)
        raise_alerts(db, p, metrics[p.id], rs)
    db.flush()
    return out


# ---------------------------------------------------------------- continuous learning
def retrain(db: Session) -> dict:
    """Champion/challenger retraining using outcomes recorded by this system.

    Real rows are RiskScore snapshots whose stage has since finished (label set by the workflow).
    The challenger is promoted only if it is not worse than the champion on the same newest-data holdout.
    """
    real = list(db.scalars(select(RiskScore).where(RiskScore.label.is_not(None))))
    if len(real) < MIN_REAL_ROWS:
        return {"promoted": False, "reason": f"Need at least {MIN_REAL_ROWS} labelled outcome(s); have {len(real)}.", "n_real": len(real)}
    real_df = pd.DataFrame(
        [
            {**{f: r.features.get(f) for f in FEATURES}, "label": r.label,
             "snapshot_date": pd.Timestamp(r.scored_at), "is_real": 1}
            for r in real
        ]
    )
    df = pd.concat([datagen.generate(), real_df], ignore_index=True)
    champion, champ_version = active_model(db)
    challenger, cm, test = mlmodel.train_model(df, real_weight=REAL_WEIGHT)
    champ_metrics = mlmodel.evaluate(champion, test)
    promote = (cm["auc"] or 0) >= (champ_metrics["auc"] or 0) - 0.002
    cm["champion_auc_same_holdout"] = champ_metrics["auc"]
    mv = _register(
        db, challenger, cm, len(real), "Retrained with recorded outcomes" + ("" if promote else " (not promoted)"), activate=promote
    )
    return {
        "promoted": promote,
        "version": mv.version,
        "previous_version": champ_version,
        "n_real": len(real),
        "challenger": cm,
        "champion": champ_metrics,
        "reason": "Challenger is at least as good on the holdout" if promote else "Challenger was worse on the holdout; kept the current model",
    }
