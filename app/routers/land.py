"""Parcels, notifications, awards, compensation and affected families."""
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, geo, integrations
from ..db import get_db
from ..models import Award, Compensation, Family, Notification, Parcel, Project, User, utcnow
from ..security import can_see_owner_names, get_current_user, get_project_or_404, require_roles
from ..workflow import STAGE_KEYS

router = APIRouter(prefix="/api", tags=["land"])


# ------------------------------------------------------------------ helpers
def parcel_dict(p: Parcel, user: User) -> dict:
    return {
        "id": p.id, "project_id": p.project_id, "survey_no": p.survey_no, "village": p.village,
        "owner_name": p.owner_name if can_see_owner_names(user) else None,
        "area_ha": round(p.area_ha, 3), "land_type": p.land_type, "status": p.status,
        "disputed": p.disputed, "possession_date": p.possession_date.isoformat() if p.possession_date else None,
    }


def _stage_at_least(project: Project, stage: str) -> bool:
    return STAGE_KEYS.index(project.stage) >= STAGE_KEYS.index(stage)


def _need_active(project: Project) -> None:
    if project.status != "active":
        raise HTTPException(409, f"Project is {project.status}; this action needs an active project")


def _parcel_project(db: Session, user: User, parcel_id: int) -> tuple[Parcel, Project]:
    parcel = db.get(Parcel, parcel_id)
    if not parcel:
        raise HTTPException(404, "Parcel not found")
    return parcel, get_project_or_404(db, user, parcel.project_id)


# ------------------------------------------------------------------ parcels
class ParcelIn(BaseModel):
    survey_no: str = Field(min_length=1, max_length=40)
    village: str = Field(default="", max_length=80)
    owner_name: str = Field(default="", max_length=120)
    area_ha: float = Field(gt=0, le=100000)
    land_type: str = "agricultural"
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    geometry: Optional[dict] = None


class ParcelPatch(BaseModel):
    disputed: Optional[bool] = None
    status: Optional[Literal["possessed"]] = None


@router.get("/projects/{project_id}/parcels")
def list_parcels(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    parcels = db.scalars(select(Parcel).where(Parcel.project_id == project.id).order_by(Parcel.id))
    return [parcel_dict(p, user) for p in parcels]


@router.post("/projects/{project_id}/parcels", status_code=201)
def add_parcel(
    project_id: int, body: ParcelIn,
    user: User = Depends(require_roles("agency", "state", "district", "field")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    _need_active(project)
    if _stage_at_least(project, "award"):
        raise HTTPException(409, "Parcels cannot be added once the award stage is reached")
    geometry = body.geometry
    if geometry is None and body.lat is not None and body.lon is not None:
        geometry = geo.rectangle(body.lat, body.lon, body.area_ha)
    parcel = Parcel(
        project_id=project.id, survey_no=body.survey_no, village=body.village, owner_name=body.owner_name,
        area_ha=body.area_ha, land_type=body.land_type, geometry=geometry,
        status="notified" if _stage_at_least(project, "survey_objections") else "proposed",
    )
    db.add(parcel)
    project.proposed_area_ha = round(project.proposed_area_ha + body.area_ha, 3)
    db.flush()
    audit.log(db, user, "parcel_added", "parcel", parcel.id, {"project_id": project.id, "survey_no": body.survey_no})
    db.commit()
    return parcel_dict(parcel, user)


@router.patch("/parcels/{parcel_id}")
def patch_parcel(
    parcel_id: int, body: ParcelPatch,
    user: User = Depends(require_roles("field", "district")),
    db: Session = Depends(get_db),
):
    parcel, project = _parcel_project(db, user, parcel_id)
    changes = {}
    if body.disputed is not None:
        changes["disputed"] = (parcel.disputed, body.disputed)
        parcel.disputed = body.disputed
    if body.status == "possessed":
        _need_active(project)
        if project.stage not in ("possession", "rr"):
            raise HTTPException(409, "Possession can only be recorded in the possession or R&R stage")
        if parcel.status != "compensated":
            raise HTTPException(409, f"Parcel is '{parcel.status}'; compensation must be completed before possession")
        changes["status"] = (parcel.status, "possessed")
        parcel.status = "possessed"
        parcel.possession_date = utcnow().date()
    if not changes:
        raise HTTPException(422, "Nothing to update")
    audit.log(db, user, "parcel_updated", "parcel", parcel.id, {k: {"from": a, "to": b} for k, (a, b) in changes.items()})
    db.commit()
    return parcel_dict(parcel, user)


@router.post("/parcels/{parcel_id}/verify")
def verify_parcel(parcel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cross-check the parcel against the (mock) land-records and cadastral services."""
    parcel, project = _parcel_project(db, user, parcel_id)
    result = integrations.verify_parcel(parcel, project)
    if not can_see_owner_names(user):
        result["record"]["owner_name"] = None
        for c in result["checks"]:
            if c["name"].startswith("Owner"):
                c["detail"] = "hidden for this role"
    audit.log(db, user, "integration_call", "parcel", parcel.id, {"sources": result["sources"], "verified": result["verified"]})
    db.commit()
    return result


# ------------------------------------------------------------------ notifications & awards
class NotificationIn(BaseModel):
    kind: Literal["preliminary", "final"] = "preliminary"
    ref_no: str = Field(min_length=1, max_length=60)
    issued_on: date
    area_ha: float = Field(default=0.0, ge=0)


class AwardIn(BaseModel):
    award_no: str = Field(min_length=1, max_length=60)
    declared_on: date
    total_area_ha: float = Field(default=0.0, ge=0)
    total_amount_cr: float = Field(default=0.0, ge=0)


@router.get("/projects/{project_id}/notifications")
def list_notifications(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    rows = db.scalars(select(Notification).where(Notification.project_id == project.id).order_by(Notification.id))
    return [{"id": n.id, "kind": n.kind, "ref_no": n.ref_no, "issued_on": n.issued_on.isoformat(), "area_ha": n.area_ha} for n in rows]


@router.post("/projects/{project_id}/notifications", status_code=201)
def add_notification(
    project_id: int, body: NotificationIn,
    user: User = Depends(require_roles("district", "state")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    _need_active(project)
    if not _stage_at_least(project, "notification"):
        raise HTTPException(409, "Notifications can be recorded once the project reaches the notification stage")
    n = Notification(project_id=project.id, **body.model_dump())
    db.add(n)
    db.flush()
    audit.log(db, user, "notification_recorded", "notification", n.id, {"project_id": project.id, "ref_no": n.ref_no})
    db.commit()
    return {"id": n.id, "ref_no": n.ref_no}


@router.get("/projects/{project_id}/awards")
def list_awards(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    rows = db.scalars(select(Award).where(Award.project_id == project.id).order_by(Award.id))
    return [{"id": a.id, "award_no": a.award_no, "declared_on": a.declared_on.isoformat(),
             "total_area_ha": a.total_area_ha, "total_amount_cr": a.total_amount_cr} for a in rows]


@router.post("/projects/{project_id}/awards", status_code=201)
def add_award(
    project_id: int, body: AwardIn,
    user: User = Depends(require_roles("district")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    _need_active(project)
    if project.stage != "award":
        raise HTTPException(409, "Awards are declared while the project is in the award stage")
    a = Award(project_id=project.id, **body.model_dump())
    db.add(a)
    db.flush()
    audit.log(db, user, "award_declared", "award", a.id, {"project_id": project.id, "award_no": a.award_no})
    db.commit()
    return {"id": a.id, "award_no": a.award_no}


# ------------------------------------------------------------------ compensation
class AssessIn(BaseModel):
    rate_cr_per_ha: float = Field(gt=0, le=1000)
    parcel_ids: Optional[list[int]] = None


class DisburseIn(BaseModel):
    amount_cr: float = Field(gt=0)


class DisburseBulkIn(BaseModel):
    fraction: float = Field(gt=0, le=1, description="share of each record's remaining balance to pay now")


def _comp_dict(c: Compensation) -> dict:
    return {
        "id": c.id, "parcel_id": c.parcel_id, "assessed_cr": round(c.assessed_cr, 3),
        "disbursed_cr": round(c.disbursed_cr, 3), "assessed_on": c.assessed_on.isoformat(),
        "last_disbursed_on": c.last_disbursed_on.isoformat() if c.last_disbursed_on else None,
    }


def _pay(db: Session, c: Compensation, amount: float) -> float:
    paid = round(min(amount, c.assessed_cr - c.disbursed_cr), 6)
    if paid <= 0:
        return 0.0
    c.disbursed_cr = round(c.disbursed_cr + paid, 6)
    c.last_disbursed_on = utcnow().date()
    if c.disbursed_cr >= c.assessed_cr - 1e-9:
        parcel = db.get(Parcel, c.parcel_id)
        if parcel and parcel.status == "awarded":
            parcel.status = "compensated"
    return paid


@router.get("/projects/{project_id}/compensation")
def list_compensation(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    rows = db.scalars(select(Compensation).where(Compensation.project_id == project.id).order_by(Compensation.id))
    return [_comp_dict(c) for c in rows]


@router.post("/projects/{project_id}/compensation/assess")
def assess_compensation(
    project_id: int, body: AssessIn,
    user: User = Depends(require_roles("district")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    _need_active(project)
    if project.stage != "compensation":
        raise HTTPException(409, "Compensation is assessed in the compensation stage")
    have = set(db.scalars(select(Compensation.parcel_id).where(Compensation.project_id == project.id)))
    q = select(Parcel).where(Parcel.project_id == project.id, Parcel.status == "awarded")
    if body.parcel_ids:
        q = q.where(Parcel.id.in_(body.parcel_ids))
    created, total = 0, 0.0
    for p in db.scalars(q):
        if p.id in have:
            continue
        amount = round(p.area_ha * body.rate_cr_per_ha, 4)
        db.add(Compensation(project_id=project.id, parcel_id=p.id, assessed_cr=amount, assessed_on=utcnow().date()))
        created += 1
        total += amount
    audit.log(db, user, "compensation_assessed", "project", project.id, {"records": created, "total_cr": round(total, 3)})
    db.commit()
    return {"created": created, "assessed_cr": round(total, 3)}


@router.post("/compensation/{comp_id}/disburse")
def disburse(
    comp_id: int, body: DisburseIn,
    user: User = Depends(require_roles("district")),
    db: Session = Depends(get_db),
):
    c = db.get(Compensation, comp_id)
    if not c:
        raise HTTPException(404, "Compensation record not found")
    project = get_project_or_404(db, user, c.project_id)
    _need_active(project)
    if project.stage != "compensation":
        raise HTTPException(409, "Disbursement happens in the compensation stage")
    paid = _pay(db, c, body.amount_cr)
    if paid <= 0:
        raise HTTPException(409, "Nothing left to disburse on this record")
    audit.log(db, user, "compensation_disbursed", "compensation", c.id, {"amount_cr": paid, "parcel_id": c.parcel_id})
    db.commit()
    return _comp_dict(c)


@router.post("/projects/{project_id}/compensation/disburse-bulk")
def disburse_bulk(
    project_id: int, body: DisburseBulkIn,
    user: User = Depends(require_roles("district")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    _need_active(project)
    if project.stage != "compensation":
        raise HTTPException(409, "Disbursement happens in the compensation stage")
    total, n = 0.0, 0
    for c in db.scalars(select(Compensation).where(Compensation.project_id == project.id)):
        paid = _pay(db, c, (c.assessed_cr - c.disbursed_cr) * body.fraction)
        if paid > 0:
            total += paid
            n += 1
    audit.log(db, user, "compensation_disbursed_bulk", "project", project.id, {"records": n, "amount_cr": round(total, 3)})
    db.commit()
    return {"records_paid": n, "amount_cr": round(total, 3)}


# ------------------------------------------------------------------ families and R&R
class FamilyIn(BaseModel):
    head_name: str = Field(min_length=1, max_length=120)
    members: int = Field(default=4, ge=1, le=50)
    displaced: bool = False
    parcel_id: Optional[int] = None
    rr_status: Literal["pending", "package_approved", "allotted", "resettled"] = "pending"


class FamilyPatch(BaseModel):
    rr_status: Literal["pending", "package_approved", "allotted", "resettled"]


def _family_dict(f: Family) -> dict:
    return {"id": f.id, "head_name": f.head_name, "members": f.members, "displaced": f.displaced,
            "parcel_id": f.parcel_id, "rr_status": f.rr_status}


@router.get("/projects/{project_id}/families")
def list_families(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    rows = db.scalars(select(Family).where(Family.project_id == project.id).order_by(Family.id))
    return [_family_dict(f) for f in rows]


@router.post("/projects/{project_id}/families", status_code=201)
def add_family(
    project_id: int, body: FamilyIn,
    user: User = Depends(require_roles("district", "field")),
    db: Session = Depends(get_db),
):
    project = get_project_or_404(db, user, project_id)
    _need_active(project)
    if body.parcel_id is not None:
        parcel = db.get(Parcel, body.parcel_id)
        if not parcel or parcel.project_id != project.id:
            raise HTTPException(422, "parcel_id does not belong to this project")
    f = Family(project_id=project.id, **body.model_dump())
    db.add(f)
    db.flush()
    audit.log(db, user, "family_added", "family", f.id, {"project_id": project.id, "displaced": f.displaced})
    db.commit()
    return _family_dict(f)


@router.patch("/families/{family_id}")
def update_family(
    family_id: int, body: FamilyPatch,
    user: User = Depends(require_roles("district", "field")),
    db: Session = Depends(get_db),
):
    f = db.get(Family, family_id)
    if not f:
        raise HTTPException(404, "Family not found")
    project = get_project_or_404(db, user, f.project_id)
    _need_active(project)
    before = f.rr_status
    f.rr_status = body.rr_status
    audit.log(db, user, "rr_status_updated", "family", f.id, {"from": before, "to": body.rr_status})
    db.commit()
    return _family_dict(f)
