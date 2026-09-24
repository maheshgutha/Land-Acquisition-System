import math

import numpy as np

from app import geo, integrations
from app.ml import datagen
from app.ml import model as mlmodel
from app.ml.features import FEATURES


def test_portfolio_is_ranked_and_excludes_completed(client, auth):
    items = client.get("/api/risk/portfolio", headers=auth("central")).json()["items"]
    scores = [i["risk_score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    assert all(i["status"] != "completed" for i in items)
    assert all(i["risk_category"] in ("low", "medium", "high", "critical") for i in items)


def test_project_risk_has_explanations(client, auth):
    top = client.get("/api/risk/portfolio", headers=auth("central")).json()["items"][0]["id"]
    body = client.get(f"/api/risk/projects/{top}", headers=auth("central")).json()
    risk = body["risk"]
    assert 0 <= risk["probability"] <= 1 and risk["score"] == round(risk["probability"] * 100)
    impacts = [abs(d["impact"]) for d in risk["drivers"]]
    assert risk["drivers"] and impacts == sorted(impacts, reverse=True)
    assert all(d["direction"] in ("increases", "reduces") for d in risk["drivers"])
    assert risk["stage_profile"][0]["current"] is True and all(0 <= s["probability"] <= 1 for s in risk["stage_profile"])
    assert "log-odds" in body["explanation_note"]


def test_alerts_are_typed_and_scoped(client, auth):
    alerts = client.get("/api/alerts", headers=auth("central")).json()
    assert alerts and all(a["source"] in ("rule", "model") for a in alerts)
    assert all(a["source"] == "model" for a in alerts if a["kind"] == "high_delay_risk")
    assert {a["kind"] for a in alerts} <= {"stalled", "stage_overdue", "compensation_stalled", "legal_disputes", "high_delay_risk"}
    ka_codes = {p["code"] for p in client.get("/api/projects", headers=auth("state_ka")).json()["items"]}
    assert {a["project_code"] for a in client.get("/api/alerts", headers=auth("state_ka")).json()} <= ka_codes


def test_rescore_all_requires_privileged_role(client, auth):
    assert client.post("/api/risk/rescore", headers=auth("field_krishna")).status_code == 403
    r = client.post("/api/risk/rescore", headers=auth("central"))
    assert r.status_code == 200 and r.json()["rescored"] > 0


def test_alert_ack_flow(client, auth):
    alerts = client.get("/api/alerts", headers=auth("central")).json()
    assert alerts
    aid = alerts[0]["id"]
    assert client.post(f"/api/alerts/{aid}/ack", headers=auth("auditor")).status_code == 403
    assert client.post(f"/api/alerts/{aid}/ack", headers=auth("central")).json()["acknowledged"] is True
    assert aid not in [a["id"] for a in client.get("/api/alerts", headers=auth("central")).json()]


def test_dashboard_matches_problem_statement_kpis(client, auth):
    s = client.get("/api/dashboard/summary", headers=auth("central")).json()
    for key in ("area_ha", "notifications", "awards", "compensation", "families", "possession", "timeline", "risk", "by_stage"):
        assert key in s
    assert s["projects"]["total"] == s["projects"]["active"] + s["projects"]["stalled"] + s["projects"]["completed"]
    assert s["area_ha"]["proposed"] >= s["area_ha"]["notified"] >= s["area_ha"]["awarded"] >= s["area_ha"]["acquired"] >= 0
    assert 0 <= s["compensation"]["disbursed_pct"] <= 100
    by_state = client.get("/api/dashboard/by-state", headers=auth("central")).json()
    assert sum(g["projects"] for g in by_state) == s["projects"]["total"]
    ka = client.get("/api/dashboard/summary", headers=auth("state_ka")).json()
    assert ka["projects"]["total"] < s["projects"]["total"]
    trends = client.get("/api/dashboard/trends", headers=auth("central")).json()
    assert trends["stage_performance"] and trends["by_district"]


def test_gis_layers(client, auth):
    fc = client.get("/api/gis/parcels", headers=auth("central")).json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) > 100
    f = fc["features"][0]
    assert f["geometry"]["type"] == "Polygon" and f["properties"]["status"] in ("proposed", "notified", "awarded", "compensated", "possessed")
    lons = [c[0] for c in f["geometry"]["coordinates"][0]]
    box = f"{min(lons) - 0.001},-90,{max(lons) + 0.001},90"
    sub = client.get("/api/gis/parcels", params={"bbox": box}, headers=auth("central")).json()["features"]
    assert 0 < len(sub) < len(fc["features"])
    assert client.get("/api/gis/parcels", params={"bbox": "nope"}, headers=auth("central")).status_code == 422
    pts = client.get("/api/gis/projects", headers=auth("central")).json()["features"]
    assert pts and pts[0]["geometry"]["type"] == "Point"


def test_mis_report_modes(client, auth):
    rows = client.get("/api/reports/mis", params={"group_by": "state"}, headers=auth("central")).json()
    assert {r["group"] for r in rows["rows"]} == {"Andhra Pradesh", "Telangana", "Karnataka"}
    assert sum(r["projects"] for r in rows["rows"]) == 36
    csv_text = client.get("/api/reports/mis", params={"format": "csv", "columns": "code,state,risk_score"}, headers=auth("central"))
    assert csv_text.headers["content-type"].startswith("text/csv")
    assert csv_text.text.splitlines()[0] == "code,state,risk_score"
    assert client.get("/api/reports/mis", params={"columns": "password_hash"}, headers=auth("central")).status_code == 422
    scoped = client.get("/api/reports/mis", headers=auth("state_ka")).json()["rows"]
    assert {r["state"] for r in scoped} == {"Karnataka"}


# ------------------------------------------------------------------ integrations, geo, model
def test_mock_land_records_are_deterministic():
    a = integrations.get_land_records().lookup("Andhra Pradesh", "Krishna", "Rampur", "12/A")
    b = integrations.get_land_records().lookup("Andhra Pradesh", "Krishna", "Rampur", "12/A")
    c = integrations.get_land_records().lookup("Andhra Pradesh", "Krishna", "Rampur", "13/A")
    assert a == b and a != c


def test_verification_catches_planted_mismatches(client, auth):
    fc = client.get("/api/gis/parcels", headers=auth("central")).json()["features"][:80]
    verdicts = [client.post(f"/api/parcels/{f['properties']['id']}/verify", headers=auth("central")).json()["verified"] for f in fc]
    failed = verdicts.count(False)
    assert 1 <= failed < len(fc) * 0.35  # roughly the ~10% planted data-quality problems, not everything


def test_rectangle_area_and_centroid():
    g = geo.rectangle(16.6, 80.7, 4.0)
    assert math.isclose(geo.geometry_area_ha(g), 4.0, rel_tol=0.01)
    lat, lon = geo.centroid(g)
    assert math.isclose(lat, 16.6, abs_tol=1e-4) and math.isclose(lon, 80.7, abs_tol=1e-4)


def test_model_beats_baseline_and_shap_adds_up():
    df = datagen.generate(n=4000, seed=7)
    bundle, metrics, test = mlmodel.train_model(df)
    assert metrics["auc"] > 0.68 and metrics["top_decile_lift"] > 1.5
    base = df["label"].mean()
    assert metrics["brier"] < base * (1 - base)  # better than always predicting the base rate
    X = test[FEATURES].head(50)
    contrib = mlmodel.contributions(bundle, X)
    assert contrib.shape == (50, len(FEATURES))
    full = bundle["model"].booster_.predict(X, pred_contrib=True)  # last column is the bias term
    raw = bundle["model"].booster_.predict(X, raw_score=True)
    assert np.allclose(full.sum(axis=1), raw, atol=1e-5)  # TreeSHAP values add up to the model's log-odds


def test_synthetic_data_has_realistic_base_rate():
    df = datagen.generate(n=3000, seed=1)
    assert 0.25 < df["label"].mean() < 0.55
    assert (df[df["overrun_ratio"] > 1.25]["label"] == 1).all()  # already past the delay line


def test_portfolio_splits_early_warnings_from_already_late(client, auth):
    early = client.get("/api/risk/portfolio", params={"delayed": "false"}, headers=auth("central")).json()["items"]
    late = client.get("/api/risk/portfolio", params={"delayed": "true"}, headers=auth("central")).json()["items"]
    assert early and late
    assert not any(p["already_delayed"] for p in early) and all(p["already_delayed"] for p in late)
    assert all(p["overrun_ratio"] <= 1.25 for p in early) and all(p["overrun_ratio"] > 1.25 for p in late)
    scores = [p["risk_score"] for p in early]
    assert scores == sorted(scores, reverse=True)
    assert client.get(f"/api/projects/{early[0]['id']}", headers=auth("central")).json()["estimated_cost_cr"] > 0
