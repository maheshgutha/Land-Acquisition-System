"""End-to-end acquisition lifecycle through the API, including every stage gate and role check."""
import pytest

NEW = {
    "name": "Test bypass, Krishna", "project_type": "highway", "state": "Andhra Pradesh", "district": "Krishna",
    "proposed_area_ha": 12.0, "estimated_cost_cr": 240.0,
}


def upload(client, h, pid, category, content=b"doc"):
    r = client.post(f"/api/projects/{pid}/documents", headers=h, files={"file": (f"{category}.txt", content)},
                    data={"category": category, "name": category})
    assert r.status_code == 201, r.text
    return r.json()


def advance(client, h, pid):
    return client.post(f"/api/projects/{pid}/advance", headers=h)


@pytest.fixture(scope="module")
def lifecycle(client, auth):
    """Create one project and walk it through all stages; returns its id and notes what happened."""
    log = {}
    r = client.post("/api/projects", json=NEW, headers=auth("agency_nhai"))
    assert r.status_code == 201, r.text
    p = r.json()
    pid = log["pid"] = p["id"]
    assert p["stage"] == "proposal" and p["code"].startswith("LA-AP-")
    assert p["risk"] is not None  # scored on creation

    # proposal: gate needs a proposal report; only agency/state may complete the stage
    r = advance(client, auth("agency_nhai"), pid)
    assert r.status_code == 409 and "Upload the proposal report" in r.json()["detail"]["unmet"]
    assert advance(client, auth("dist_krishna"), pid).status_code == 403
    upload(client, auth("agency_nhai"), pid, "proposal_report")
    for i in range(3):
        r = client.post(f"/api/projects/{pid}/parcels", headers=auth("agency_nhai"), json={
            "survey_no": f"T{i}/A", "village": "Rampur", "owner_name": f"Owner {i}", "area_ha": 3.0,
            "lat": 16.6 + i * 0.01, "lon": 80.7})
        assert r.status_code == 201, r.text
    assert advance(client, auth("agency_nhai"), pid).status_code == 200

    # scrutiny: at least 4 of 5 required documents
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409 and "missing" in r.json()["detail"]["unmet"][0]
    for cat in ("land_plan", "cost_estimate", "environment_clearance", "social_impact_assessment"):
        upload(client, auth("agency_nhai"), pid, cat)
    assert advance(client, auth("agency_nhai"), pid).status_code == 403  # agency cannot approve scrutiny
    assert advance(client, auth("dist_krishna"), pid).status_code == 200

    # approval: state or central only
    assert advance(client, auth("field_krishna"), pid).status_code == 403
    assert advance(client, auth("dist_krishna"), pid).status_code == 403
    assert advance(client, auth("state_ap"), pid).status_code == 200

    # notification: needs a recorded notification
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409 and "notification" in r.json()["detail"]["unmet"][0].lower()
    r = client.post(f"/api/projects/{pid}/notifications", headers=auth("dist_krishna"),
                    json={"kind": "preliminary", "ref_no": "N/1", "issued_on": "2026-09-01", "area_ha": 9})
    assert r.status_code == 201
    assert advance(client, auth("dist_krishna"), pid).status_code == 200
    parcels = client.get(f"/api/projects/{pid}/parcels", headers=auth("dist_krishna")).json()
    assert {x["status"] for x in parcels} == {"notified"}

    # survey and objections, then award
    assert advance(client, auth("field_krishna"), pid).status_code == 200
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409  # no award yet
    r = client.post(f"/api/projects/{pid}/awards", headers=auth("dist_krishna"),
                    json={"award_no": "AW/1", "declared_on": "2026-09-15", "total_area_ha": 9, "total_amount_cr": 8})
    assert r.status_code == 201
    assert advance(client, auth("dist_krishna"), pid).status_code == 200
    assert {x["status"] for x in client.get(f"/api/projects/{pid}/parcels", headers=auth("dist_krishna")).json()} == {"awarded"}

    # compensation: 95% of assessed must be disbursed
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409 and "Assess compensation" in r.json()["detail"]["unmet"][0]
    r = client.post(f"/api/projects/{pid}/compensation/assess", headers=auth("dist_krishna"), json={"rate_cr_per_ha": 0.8})
    assert r.json()["created"] == 3 and r.json()["assessed_cr"] == pytest.approx(7.2)
    client.post(f"/api/projects/{pid}/compensation/disburse-bulk", headers=auth("dist_krishna"), json={"fraction": 0.5})
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409 and "50%" in r.json()["detail"]["unmet"][0]
    client.post(f"/api/projects/{pid}/compensation/disburse-bulk", headers=auth("dist_krishna"), json={"fraction": 1.0})
    assert {x["status"] for x in client.get(f"/api/projects/{pid}/parcels", headers=auth("dist_krishna")).json()} == {"compensated"}
    assert advance(client, auth("dist_krishna"), pid).status_code == 200

    # possession: every parcel must be possessed
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409 and "3 parcel" in r.json()["detail"]["unmet"][0]
    for x in parcels:
        r = client.patch(f"/api/parcels/{x['id']}", headers=auth("field_krishna"), json={"status": "possessed"})
        assert r.status_code == 200, r.text
    assert advance(client, auth("dist_krishna"), pid).status_code == 200

    # rehabilitation and resettlement: displaced families must be resettled
    fam = client.post(f"/api/projects/{pid}/families", headers=auth("field_krishna"),
                      json={"head_name": "Test Family", "members": 5, "displaced": True}).json()
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 409 and "not yet resettled" in r.json()["detail"]["unmet"][0]
    for status in ("package_approved", "allotted", "resettled"):
        assert client.patch(f"/api/families/{fam['id']}", headers=auth("field_krishna"), json={"rr_status": status}).status_code == 200
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code == 200
    log["final"] = r.json()
    return log


def test_full_lifecycle_completes(client, auth, lifecycle):
    final = lifecycle["final"]
    assert final["stage"] == "closeout" and final["status"] == "completed" and final["progress_pct"] == 100.0
    assert all(s["state"] == "done" for s in final["stages"])
    kinds = [e["kind"] for e in final["events"]]
    assert kinds.count("stage_change") == 9 and "created" in kinds


def test_completed_project_cannot_advance_or_stall(client, auth, lifecycle):
    pid = lifecycle["pid"]
    assert advance(client, auth("dist_krishna"), pid).status_code == 409
    r = client.post(f"/api/projects/{pid}/stall", json={"reason": "other"}, headers=auth("dist_krishna"))
    assert r.status_code == 409


def test_stage_outcomes_were_recorded_as_labels(lifecycle):
    from app.db import SessionLocal
    from app.models import RiskScore
    with SessionLocal() as db:
        rows = db.query(RiskScore).filter(RiskScore.project_id == lifecycle["pid"]).all()
    labelled = [r for r in rows if r.label is not None]
    assert labelled and all(r.label in (0, 1) for r in labelled)  # snapshots taken at earlier stages got real outcomes


def test_retrain_uses_recorded_outcomes(client, auth, lifecycle):
    r = client.post("/api/risk/retrain", headers=auth("central"))
    assert r.status_code == 200
    body = r.json()
    assert body["n_real"] >= 1 and "promoted" in body and "champion" in body and "challenger" in body
    info = client.get("/api/risk/model", headers=auth("central")).json()
    assert info["has_recorded_outcomes"] is True and "SYNTHETIC" in info["training_data_note"]
    assert sum(v["active"] for v in info["versions"]) == 1  # exactly one champion


def test_stall_and_resume(client, auth):
    pid = next(p["id"] for p in client.get("/api/projects", params={"status": "active"}, headers=auth("dist_krishna")).json()["items"])
    assert client.post(f"/api/projects/{pid}/stall", json={"reason": "not-a-reason"}, headers=auth("dist_krishna")).status_code == 422
    r = client.post(f"/api/projects/{pid}/stall", json={"reason": "court_stay", "note": "HC stay order"}, headers=auth("dist_krishna"))
    assert r.status_code == 200 and r.json()["status"] == "stalled"
    # stalled projects cannot advance
    r = advance(client, auth("dist_krishna"), pid)
    assert r.status_code in (403, 409)
    why = client.get(f"/api/projects/{pid}/why", headers=auth("central")).json()
    assert why["has_recorded_reason"] is True
    assert any("Court stay" in f["text"] for f in why["recorded_facts"])
    assert "prediction" in why["note"].lower()
    alerts = client.get("/api/alerts", headers=auth("dist_krishna")).json()
    assert any(a["project_id"] == pid and a["kind"] == "stalled" for a in alerts)
    r = client.post(f"/api/projects/{pid}/resume", json={"text": "Stay vacated"}, headers=auth("dist_krishna"))
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.post(f"/api/projects/{pid}/resume", headers=auth("dist_krishna")).status_code == 409


def test_why_endpoint_separates_facts_from_inference(client, auth):
    pid = client.get("/api/risk/portfolio", headers=auth("central")).json()["items"][0]["id"]
    why = client.get(f"/api/projects/{pid}/why", headers=auth("central")).json()
    assert set(why) >= {"summary", "recorded_facts", "rule_findings", "model_inference", "recommendations", "note"}


def test_patch_project_inputs_rescores(client, auth):
    pid = client.get("/api/projects", params={"status": "active"}, headers=auth("dist_guntur")).json()["items"][0]["id"]
    before = client.get(f"/api/projects/{pid}", headers=auth("dist_guntur")).json()
    r = client.patch(f"/api/projects/{pid}", json={"legal_disputes": before["legal_disputes"] + 3}, headers=auth("dist_guntur"))
    assert r.status_code == 200
    assert r.json()["legal_disputes"] == before["legal_disputes"] + 3
    assert r.json()["risk"]["score"] >= before["risk"]["score"]
    assert client.patch(f"/api/projects/{pid}", json={}, headers=auth("dist_guntur")).status_code == 422
    assert client.patch(f"/api/projects/{pid}", json={"legal_disputes": -1}, headers=auth("dist_guntur")).status_code == 422
