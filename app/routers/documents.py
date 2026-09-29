"""Document repository with versions, checksums and audit history."""
import hashlib
import io
import re
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import ALLOWED_UPLOAD_EXTENSIONS, MAX_UPLOAD_MB, STORAGE_DIR
from ..db import get_db
from ..ml import ocr as ocr_engine
from ..models import Document, DocumentExtraction, DocumentVersion, Parcel, Project, User, utcnow
from ..security import can_see_owner_names, get_current_user, get_project_or_404, project_scope, require_roles
from ..workflow import REQUIRED_DOC_CATEGORIES

router = APIRouter(prefix="/api", tags=["documents"])

CATEGORIES = REQUIRED_DOC_CATEGORIES + [
    "notification_copy", "award_copy", "survey_report", "court_order", "payment_proof", "other",
]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:120] or "file"


def _version_dict(v: DocumentVersion) -> dict:
    return {"id": v.id, "version": v.version, "filename": v.filename, "sha256": v.sha256, "size": v.size,
            "uploaded_by": v.uploaded_by, "uploaded_at": v.uploaded_at.isoformat(), "note": v.note}


def _doc_dict(d: Document) -> dict:
    return {"id": d.id, "name": d.name, "category": d.category, "created_at": d.created_at.isoformat(),
            "latest_version": d.versions[-1].version if d.versions else 0,
            "versions": [_version_dict(v) for v in d.versions]}


def _extraction_dict(e: DocumentExtraction) -> dict:
    fields = e.fields or {}
    return {
        "id": e.id, "document_version_id": e.document_version_id, "status": e.status,
        "engine": e.engine, "ocr_confidence": e.ocr_confidence, "text": e.text, "fields": fields,
        "fields_found": len(fields), "fields_total": len(ocr_engine.ALL_FIELDS),
        # For a finished extraction the note holds reviewer warnings, one per line.
        "warnings": [w for w in (e.note or "").splitlines() if w] if e.status == "done" else [],
        "note": e.note, "applied": e.applied, "applied_by": e.applied_by,
        "applied_at": e.applied_at.isoformat() if e.applied_at else None,
        "created_at": e.created_at.isoformat(),
    }


def store_ocr_result(db: Session, ver: DocumentVersion, result: dict) -> DocumentExtraction:
    """Stores an OCR/extraction result (from ocr_engine.run_ocr) against this version."""
    note = result.get("reason") or result.get("error") or "\n".join(result.get("warnings", []))
    extraction = DocumentExtraction(
        document_version_id=ver.id,
        status=result["status"],
        engine=result.get("engine", ""),
        ocr_confidence=result.get("ocr_confidence"),
        text=result.get("text", ""),
        fields=result.get("fields", {}),
        note=note,
    )
    db.add(extraction)
    db.flush()
    return extraction


def write_version(db: Session, project_id: int, name: str, category: str, filename: str, data: bytes,
                  user: User | str, note: str = "") -> tuple[Document, DocumentVersion]:
    """Store `data` as a new version of the named document (created on first upload)."""
    doc = db.scalar(select(Document).where(Document.project_id == project_id, Document.name == name))
    if doc is None:
        doc = Document(project_id=project_id, name=name, category=category)
        db.add(doc)
        db.flush()
    version = (doc.versions[-1].version + 1) if doc.versions else 1
    folder = STORAGE_DIR / f"project_{project_id}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"doc{doc.id}_v{version}_{_safe(filename)}"
    path.write_bytes(data)
    uploader = user.username if isinstance(user, User) else user
    ver = DocumentVersion(
        document_id=doc.id, version=version, filename=_safe(filename), stored_path=str(path),
        sha256=hashlib.sha256(data).hexdigest(), size=len(data), uploaded_by=uploader, note=note,
    )
    db.add(ver)
    db.flush()
    doc.versions.append(ver)
    return doc, ver


@router.get("/projects/{project_id}/documents")
def list_documents(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    docs = list(db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.id)))
    version_ids = [v.id for d in docs for v in d.versions]
    extractions = {
        e.document_version_id: e for e in db.scalars(
            select(DocumentExtraction).where(DocumentExtraction.document_version_id.in_(version_ids)))
    } if version_ids else {}
    out = []
    for d in docs:
        item = _doc_dict(d)
        for v in item["versions"]:
            e = extractions.get(v["id"])
            v["ocr"] = {"status": e.status, "fields_found": len(e.fields or {}), "applied": e.applied} if e else None
        out.append(item)
    return out


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    category: str = Form("other"),
    name: Optional[str] = Form(None),
    note: str = Form(""),
    user: User = Depends(require_roles("agency", "state", "district", "field")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    if category not in CATEGORIES:
        raise HTTPException(422, f"Unknown category. Allowed: {', '.join(CATEGORIES)}")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(422, f"File type '{ext or 'unknown'}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}")
    limit = MAX_UPLOAD_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(422, "Empty file")
    doc_name = (name or file.filename or "document").strip()[:200]
    doc, ver = write_version(db, project.id, doc_name, category, file.filename or doc_name, data, user, note)
    audit.log(db, user, "document_uploaded", "document", doc.id,
              {"project_id": project.id, "version": ver.version, "sha256": ver.sha256, "category": category})
    # OCR is CPU-bound (about 1 s a page); run it off the event loop so one upload never
    # freezes the server for everyone else.
    result = await run_in_threadpool(ocr_engine.run_ocr, data, ver.filename)
    extraction = store_ocr_result(db, ver, result)
    audit.log(db, user, "document_ocr_processed", "document", doc.id,
              {"version": ver.version, "status": extraction.status, "fields_found": list(extraction.fields.keys())})
    db.commit()
    out = _doc_dict(doc)
    out["latest_extraction"] = _extraction_dict(extraction)
    return out


@router.get("/documents/versions/{version_id}/extraction")
def get_extraction(version_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ver = db.get(DocumentVersion, version_id)
    if not ver:
        raise HTTPException(404, "Version not found")
    doc = db.get(Document, ver.document_id)
    get_project_or_404(db, user, doc.project_id)
    extraction = db.scalar(select(DocumentExtraction).where(DocumentExtraction.document_version_id == version_id))
    if not extraction:
        raise HTTPException(404, "No OCR extraction was run for this version")
    return _extraction_dict(extraction)


@router.post("/documents/versions/{version_id}/apply-extraction")
def apply_extraction(
    version_id: int,
    fields: dict = Body(..., embed=True),
    user: User = Depends(require_roles("district", "state", "central")),
    db: Session = Depends(get_db),
):
    """An officer reviews the OCR'd fields (editing any that are wrong) and confirms
    them onto the matching parcel. This is the human-in-the-loop step: nothing from
    OCR ever reaches a parcel record without a reviewing officer's sign-off, and the
    action is fully audited."""
    ver = db.get(DocumentVersion, version_id)
    if not ver:
        raise HTTPException(404, "Version not found")
    doc = db.get(Document, ver.document_id)
    project = get_project_or_404(db, user, doc.project_id)
    extraction = db.scalar(select(DocumentExtraction).where(DocumentExtraction.document_version_id == version_id))
    if not extraction:
        raise HTTPException(404, "No OCR extraction was run for this version")

    survey_no = re.sub(r"\s+", "", str(fields.get("survey_no") or "")).upper()
    if not survey_no:
        raise HTTPException(422, "A survey_no is required to match this document to a parcel")
    parcel = db.scalar(
        select(Parcel).where(Parcel.project_id == project.id,
                             func.upper(func.replace(Parcel.survey_no, " ", "")) == survey_no)
    )
    if not parcel:
        raise HTTPException(404, f"No parcel with survey number '{survey_no}' on this project")

    updated = {}
    if fields.get("owner_name"):
        parcel.owner_name = fields["owner_name"][:120]
        updated["owner_name"] = parcel.owner_name
    if fields.get("village"):
        parcel.village = fields["village"][:80]
        updated["village"] = parcel.village
    if fields.get("area_ha"):
        try:
            area = float(fields["area_ha"])
        except (TypeError, ValueError):
            raise HTTPException(422, "area_ha must be numeric")
        if not 0 < area <= 100000:
            raise HTTPException(422, "area_ha must be between 0 and 100000 hectares")
        parcel.area_ha = area
        updated["area_ha"] = parcel.area_ha
    if fields.get("land_type"):
        if fields["land_type"] not in ocr_engine.PARCEL_LAND_TYPES:
            raise HTTPException(422, f"land_type must be one of: {', '.join(ocr_engine.PARCEL_LAND_TYPES)}")
        parcel.land_type = fields["land_type"]
        updated["land_type"] = parcel.land_type

    extraction.applied = True
    extraction.applied_by = user.username
    extraction.applied_at = utcnow()
    audit.log(db, user, "document_extraction_applied", "parcel", parcel.id,
              {"document_id": doc.id, "version": ver.version, "updated_fields": updated})
    db.commit()
    return {"parcel_id": parcel.id, "updated_fields": updated}


@router.get("/documents/extractions")
def list_extractions(limit: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Recently digitized documents across every project the caller can see (newest first)."""
    q = (select(DocumentExtraction, DocumentVersion, Document, Project)
         .join(DocumentVersion, DocumentExtraction.document_version_id == DocumentVersion.id)
         .join(Document, DocumentVersion.document_id == Document.id)
         .join(Project, Document.project_id == Project.id)
         .order_by(DocumentExtraction.id.desc()).limit(limit))
    cond = project_scope(user)
    if cond is not None:
        q = q.where(cond)
    items = []
    for e, v, d, p in db.execute(q):
        items.append({
            "version_id": v.id, "version": v.version, "filename": v.filename, "uploaded_by": v.uploaded_by,
            "uploaded_at": v.uploaded_at.isoformat(), "document_id": d.id, "document_name": d.name,
            "category": d.category, "project_id": p.id, "project_code": p.code, "project_name": p.name,
            "status": e.status, "ocr_confidence": e.ocr_confidence, "fields_found": len(e.fields or {}),
            "fields_total": len(ocr_engine.ALL_FIELDS), "survey_no": (e.fields or {}).get("survey_no", {}).get("value"),
            "applied": e.applied, "applied_by": e.applied_by,
        })
    return {"items": items}


def _inr(n: int) -> str:
    """Indian digit grouping: 3479000 -> 34,79,000."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head] + groups + [tail]) if head else ",".join(groups + [tail])


@router.get("/projects/{project_id}/sample-scan")
def sample_scan(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A synthetic, scanned-looking award notice for one of this project's real parcels, so the
    OCR flow can be tried end to end (upload -> fields read -> officer applies to the parcel)."""
    from PIL import Image, ImageDraw, ImageFont

    project = get_project_or_404(db, user, project_id)
    parcel = db.scalar(select(Parcel).where(Parcel.project_id == project.id).order_by(Parcel.id))
    if not parcel:
        raise HTTPException(404, "This project has no parcels yet")
    owner = parcel.owner_name if can_see_owner_names(user) and parcel.owner_name else "Sample Owner"
    acres = parcel.area_ha / ocr_engine.ACRE_TO_HA
    amount = int(round(parcel.area_ha * 2_450_000, -3))
    lines = [
        ("SAMPLE AWARD NOTICE - SYNTHETIC DEMO DOCUMENT", 34),
        ("Not a government record. Generated by LandScan for testing OCR.", 22),
        ("", 16),
        (f"Project: {project.code}", 28),
        (f"Award No: AWD/{project.code}/{parcel.id:04d}", 28),
        (f"Dated: {date.today():%d/%m/%Y}", 28),
        ("", 16),
        (f"Survey No: {parcel.survey_no}", 30),
        (f"Village: {parcel.village or 'Sample Village'}, District: {project.district}", 30),
        (f"Owner Name: {owner}", 30),
        (f"Area: {acres:.2f} Acres", 30),
        (f"Land Type: {parcel.land_type.title()}", 30),
        (f"Compensation: Rs. {_inr(amount)}/-", 30),
        ("", 16),
        ("Collector (Land Acquisition)  -  SPECIMEN, NOT VALID", 22),
    ]
    img = Image.new("L", (1240, 1000), 250)
    draw = ImageDraw.Draw(img)
    y = 70
    for text, size in lines:
        if text:
            draw.text((80, y), text, font=ImageFont.load_default(size=size), fill=25)
        y += int(size * 1.9)
    # Make it look like a real scan: a slight tilt, which the OCR pipeline straightens.
    img = img.rotate(1.2, resample=Image.BICUBIC, fillcolor=250)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="sample_award_{project.code}.png"'})


@router.get("/documents/versions/{version_id}/download")
def download_version(version_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ver = db.get(DocumentVersion, version_id)
    if not ver:
        raise HTTPException(404, "Version not found")
    doc = db.get(Document, ver.document_id)
    get_project_or_404(db, user, doc.project_id)
    path = Path(ver.stored_path)
    if not path.exists():
        raise HTTPException(410, "File is missing from storage")
    audit.log(db, user, "document_downloaded", "document", doc.id, {"version": ver.version})
    db.commit()
    return FileResponse(path, filename=ver.filename)
