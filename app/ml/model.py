"""Training, calibration, scoring and TreeSHAP contributions for the delay-risk model.

Model: LightGBM gradient boosting, probability calibrated with a Platt-style logistic fit
on a later time slice. Explanations are TreeSHAP values from LightGBM (`pred_contrib`),
reported in log-odds: positive pushes the delay probability up, negative pushes it down.
"""
import warnings
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .features import FEATURES

warnings.filterwarnings("ignore", message="X does not have valid feature names")
warnings.filterwarnings("ignore", message=".*eval_set.*deprecated.*")


def split_time(df: pd.DataFrame):
    """Chronological 70/15/15 split. Random splits would leak the future into training."""
    d = df.sort_values("snapshot_date").reset_index(drop=True)
    n = len(d)
    return d.iloc[: int(n * 0.70)], d.iloc[int(n * 0.70): int(n * 0.85)], d.iloc[int(n * 0.85):]


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p)).reshape(-1, 1)


def predict(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    raw = bundle["model"].predict_proba(X[bundle["features"]])[:, 1]
    cal = bundle["calibrator"].predict_proba(_logit(raw))[:, 1]
    return np.clip(cal, 0.01, 0.99)


def contributions(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    """TreeSHAP values, shape (n_rows, n_features), in log-odds."""
    contrib = bundle["model"].booster_.predict(X[bundle["features"]], pred_contrib=True)
    return np.asarray(contrib)[:, :-1]


def evaluate(bundle: dict, df: pd.DataFrame) -> dict:
    y = df["label"].to_numpy()
    p = predict(bundle, df)
    pred = (p >= 0.5).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    top = max(1, len(df) // 10)
    top_rate = float(y[np.argsort(-p)[:top]].mean())
    out = {
        "n": int(len(df)),
        "prevalence": round(float(y.mean()), 3),
        "auc": round(float(roc_auc_score(y, p)), 4) if len(set(y)) > 1 else None,
        "pr_auc": round(float(average_precision_score(y, p)), 4) if len(set(y)) > 1 else None,
        "brier": round(float(brier_score_loss(y, p)), 4),
        "precision_at_0.5": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall_at_0.5": round(tp / (tp + fn), 3) if tp + fn else None,
        # Of the 10% of cases ranked riskiest, how many were really delayed: the intervention-priority view.
        "top_decile_delay_rate": round(top_rate, 3),
        "top_decile_lift": round(top_rate / float(y.mean()), 2) if y.mean() > 0 else None,
    }
    return out


def train_model(df: pd.DataFrame, real_weight: float | None = None, seed: int = 42):
    """Train on the older 70%, calibrate on the next 15%, report on the newest 15%.

    `real_weight` up-weights rows with is_real == 1 (outcomes recorded by this system)
    relative to synthetic rows.
    """
    train, val, test = split_time(df)
    w_train = None
    if real_weight is not None:
        w_train = np.where(train["is_real"].to_numpy() == 1, float(real_weight), 1.0)
    clf = lgb.LGBMClassifier(
        n_estimators=400, learning_rate=0.04, num_leaves=15, min_child_samples=30,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=2.0,
        random_state=seed, verbose=-1,
    )
    clf.fit(
        train[FEATURES], train["label"], sample_weight=w_train,
        eval_set=[(val[FEATURES], val["label"])],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    raw_val = clf.predict_proba(val[FEATURES])[:, 1]
    calibrator = LogisticRegression(C=1e6).fit(_logit(raw_val), val["label"])
    bundle = {"model": clf, "calibrator": calibrator, "features": FEATURES}
    metrics = evaluate(bundle, test)
    metrics.update({"n_train": int(len(train)), "n_val": int(len(val)), "best_iteration": int(clf.best_iteration_ or 0)})
    return bundle, metrics, test


def save_bundle(bundle: dict, path: Path) -> None:
    joblib.dump(bundle, path)


def load_bundle(path: Path) -> dict:
    return joblib.load(path)
