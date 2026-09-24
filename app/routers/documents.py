"""Document repository with versions, checksums and audit history."""
import hashlib
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import MAX_UPLOAD_MB, STORAGE_DIR
from ..db import get_db
from ..models import Document, DocumentVersion, User
from ..security import get_current_user, get_project_or_404, require_roles
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
    docs = db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.id))
    return [_doc_dict(d) for d in docs]


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
    db.commit()
    return _doc_dict(doc)


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
