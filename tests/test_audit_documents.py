import hashlib

from app.db import SessionLocal
from app.models import AuditLog


def test_audit_chain_valid_then_detects_tampering(client, auth):
    h = auth("auditor")
    ok = client.get("/api/audit/verify", headers=h).json()
    assert ok["valid"] is True and ok["checked"] > 30

    with SessionLocal() as db:
        row = db.query(AuditLog).order_by(AuditLog.id).offset(5).first()
        rid, original = row.id, dict(row.detail)
        row.detail = {"tampered": True}
        db.commit()
    try:
        bad = client.get("/api/audit/verify", headers=h).json()
        assert bad["valid"] is False and bad["broken_at"] == rid
    finally:
        with SessionLocal() as db:
            db.get(AuditLog, rid).detail = original
            db.commit()
    assert client.get("/api/audit/verify", headers=h).json()["valid"] is True


def test_audit_deleting_a_row_is_detected(client, auth):
    with SessionLocal() as db:
        row = db.query(AuditLog).order_by(AuditLog.id).offset(10).first()
        saved = dict(id=row.id, ts=row.ts, actor=row.actor, role=row.role, action=row.action, entity_type=row.entity_type,
                     entity_id=row.entity_id, detail=row.detail, prev_hash=row.prev_hash, hash=row.hash)
        db.delete(row)
        db.commit()
    try:
        assert client.get("/api/audit/verify", headers=auth("auditor")).json()["valid"] is False
    finally:
        with SessionLocal() as db:
            db.add(AuditLog(**saved))
            db.commit()
    assert client.get("/api/audit/verify", headers=auth("auditor")).json()["valid"] is True


def test_document_versions_and_checksums(client, auth):
    pid = client.get("/api/projects", headers=auth("agency_nhai")).json()["items"][0]["id"]
    h = auth("agency_nhai")
    v1, v2 = b"first draft", b"second draft, revised"
    for data in (v1, v2):
        r = client.post(f"/api/projects/{pid}/documents", headers=h, files={"file": ("report.txt", data)},
                        data={"category": "survey_report", "name": "Survey report X", "note": "n"})
        assert r.status_code == 201
    docs = [d for d in client.get(f"/api/projects/{pid}/documents", headers=h).json() if d["name"] == "Survey report X"]
    assert len(docs) == 1 and docs[0]["latest_version"] == 2
    assert [v["sha256"] for v in docs[0]["versions"]] == [hashlib.sha256(v1).hexdigest(), hashlib.sha256(v2).hexdigest()]
    got = client.get(f"/api/documents/versions/{docs[0]['versions'][0]['id']}/download", headers=h)
    assert got.status_code == 200 and got.content == v1  # old versions stay retrievable
    log = client.get("/api/audit", params={"action": "document_uploaded"}, headers=auth("auditor")).json()
    assert log["total"] >= 2


def test_document_validation_and_scope(client, auth):
    pid = client.get("/api/projects", headers=auth("agency_nhai")).json()["items"][0]["id"]
    h = auth("agency_nhai")
    assert client.post(f"/api/projects/{pid}/documents", headers=h, files={"file": ("a.txt", b"x")},
                       data={"category": "bogus"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/documents", headers=h, files={"file": ("a.txt", b"")},
                       data={"category": "other"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/documents", headers=auth("auditor"), files={"file": ("a.txt", b"x")},
                       data={"category": "other"}).status_code == 403
    other = next(p["id"] for p in client.get("/api/projects", headers=auth("central")).json()["items"] if p["agency"] != "NHAI")
    assert client.post(f"/api/projects/{other}/documents", headers=h, files={"file": ("a.txt", b"x")},
                       data={"category": "other"}).status_code == 404


def test_document_upload_rejects_disallowed_file_type(client, auth):
    pid = client.get("/api/projects", headers=auth("agency_nhai")).json()["items"][0]["id"]
    h = auth("agency_nhai")
    r = client.post(f"/api/projects/{pid}/documents", headers=h, files={"file": ("script.exe", b"x")},
                    data={"category": "other"})
    assert r.status_code == 422 and "not allowed" in r.json()["detail"]
    ok = client.post(f"/api/projects/{pid}/documents", headers=h, files={"file": ("plan.pdf", b"%PDF-1.4 x")},
                     data={"category": "other"})
    assert ok.status_code == 201


def test_uploaded_filename_cannot_escape_storage(client, auth):
    pid = client.get("/api/projects", headers=auth("agency_nhai")).json()["items"][0]["id"]
    r = client.post(f"/api/projects/{pid}/documents", headers=auth("agency_nhai"),
                    files={"file": ("../../etc/passwd.txt", b"x")}, data={"category": "other", "name": "traversal"})
    assert r.status_code == 201
    v = r.json()["versions"][-1]
    assert "/" not in v["filename"] and ".." not in v["filename"].replace("_", "")
