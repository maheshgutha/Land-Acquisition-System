def test_login_and_me(client, auth):
    r = client.get("/api/auth/me", headers=auth("state_ka"))
    assert r.status_code == 200
    assert r.json()["role"] == "state" and r.json()["state"] == "Karnataka"


def test_bad_password_is_rejected_and_audited(client, auth):
    r = client.post("/api/auth/login", data={"username": "central", "password": "wrong"})
    assert r.status_code == 401
    rows = client.get("/api/audit", params={"action": "login_failed"}, headers=auth("auditor")).json()
    assert rows["total"] >= 1


def test_no_token_and_garbage_token(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_central_sees_everything_state_sees_own_state(client, auth):
    allp = client.get("/api/projects", headers=auth("central")).json()
    assert allp["total"] == 36
    ka = client.get("/api/projects", headers=auth("state_ka")).json()
    assert 0 < ka["total"] < allp["total"]
    assert {p["state"] for p in ka["items"]} == {"Karnataka"}


def test_district_and_agency_scoping(client, auth):
    d = client.get("/api/projects", headers=auth("dist_krishna")).json()
    assert d["total"] == 5 and {p["district"] for p in d["items"]} == {"Krishna"}
    a = client.get("/api/projects", headers=auth("agency_nhai")).json()
    assert a["total"] > 0 and {p["agency"] for p in a["items"]} == {"NHAI"}


def test_out_of_scope_project_is_404_not_403(client, auth):
    ka_ids = {p["id"] for p in client.get("/api/projects", headers=auth("state_ka")).json()["items"]}
    other = next(p["id"] for p in client.get("/api/projects", headers=auth("central")).json()["items"] if p["id"] not in ka_ids)
    for path in (f"/api/projects/{other}", f"/api/projects/{other}/parcels", f"/api/projects/{other}/why",
                 f"/api/risk/projects/{other}", f"/api/projects/{other}/documents"):
        assert client.get(path, headers=auth("state_ka")).status_code == 404, path


def test_owner_names_hidden_from_agencies(client, auth):
    pid = client.get("/api/projects", headers=auth("agency_nhai")).json()["items"][0]["id"]
    agency_view = client.get(f"/api/projects/{pid}/parcels", headers=auth("agency_nhai")).json()
    assert agency_view and all(p["owner_name"] is None for p in agency_view)
    central_view = client.get(f"/api/projects/{pid}/parcels", headers=auth("central")).json()
    assert all(p["owner_name"] for p in central_view)
    gis = client.get("/api/gis/parcels", headers=auth("agency_nhai")).json()["features"]
    assert gis and all(f["properties"]["owner_name"] is None for f in gis)
    ver = client.post(f"/api/parcels/{agency_view[0]['id']}/verify", headers=auth("agency_nhai")).json()
    assert ver["record"]["owner_name"] is None


def test_role_restrictions(client, auth):
    pid = client.get("/api/projects", headers=auth("central")).json()["items"][0]["id"]
    assert client.post(f"/api/projects/{pid}/advance", headers=auth("auditor")).status_code == 403
    assert client.post(f"/api/projects/{pid}/stall", json={"reason": "other"}, headers=auth("auditor")).status_code == 403
    assert client.post("/api/risk/retrain", headers=auth("state_ap")).status_code == 403
    assert client.get("/api/audit", headers=auth("agency_nhai")).status_code == 403
    assert client.post("/api/projects", headers=auth("dist_krishna"), json={
        "name": "Nope", "project_type": "highway", "state": "Andhra Pradesh", "district": "Krishna",
        "proposed_area_ha": 5, "estimated_cost_cr": 10}).status_code == 403


def test_state_user_cannot_create_in_other_state(client, auth):
    r = client.post("/api/projects", headers=auth("state_ap"), json={
        "name": "Cross-state attempt", "project_type": "highway", "state": "Karnataka", "agency": "NHAI",
        "district": "Kolar", "proposed_area_ha": 5, "estimated_cost_cr": 10})
    assert r.status_code == 403


def test_demo_users_listed(client):
    users = client.get("/api/auth/demo-users").json()
    assert {u["role"] for u in users} == {"central", "state", "district", "field", "agency", "auditor"}
    assert all("password_hash" not in u for u in users)
