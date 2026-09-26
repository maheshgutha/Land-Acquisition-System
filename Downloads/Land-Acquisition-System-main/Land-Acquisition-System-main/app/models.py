"""Relational model for the land acquisition lifecycle.

Geometry is stored as GeoJSON in a JSON column so the prototype runs on SQLite.
To move to PostGIS, change `Parcel.geometry` to a GeoAlchemy2 Geometry column;
the API already speaks GeoJSON.
"""
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite friendly)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(120), default="")
    # central | state | district | field | agency | auditor
    role: Mapped[str] = mapped_column(String(20), index=True)
    state: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    agency: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    project_type: Mapped[str] = mapped_column(String(40), index=True)
    agency: Mapped[str] = mapped_column(String(120), index=True)
    state: Mapped[str] = mapped_column(String(60), index=True)
    district: Mapped[str] = mapped_column(String(60), index=True)
    template_key: Mapped[str] = mapped_column(String(40), default="default")

    stage: Mapped[str] = mapped_column(String(30), default="proposal", index=True)
    stage_entered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|stalled|completed
    stall_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    stall_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stalled_since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    proposed_area_ha: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_cr: Mapped[float] = mapped_column(Float, default=0.0)
    planned_completion: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Inputs the predictive model uses and field officers keep up to date.
    legal_disputes: Mapped[int] = mapped_column(Integer, default=0)
    approvals_pending: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_days: Mapped[float] = mapped_column(Float, default=5.0)

    parcels: Mapped[list["Parcel"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    milestones: Mapped[list["Milestone"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Milestone.id"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Event.id"
    )


class Parcel(Base):
    __tablename__ = "parcels"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    survey_no: Mapped[str] = mapped_column(String(40))
    village: Mapped[str] = mapped_column(String(80), default="")
    owner_name: Mapped[str] = mapped_column(String(120), default="")
    area_ha: Mapped[float] = mapped_column(Float)
    land_type: Mapped[str] = mapped_column(String(30), default="agricultural")
    # proposed | notified | awarded | compensated | possessed
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    possession_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    geometry: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # GeoJSON Polygon
    # verified | in_review | failed
    validation_status: Mapped[str] = mapped_column(String(20), default="verified", index=True)
    area_mismatch_pct: Mapped[float] = mapped_column(Float, default=0.0)
    doc_area_ha: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    project: Mapped[Project] = relationship(back_populates="parcels")
    extractions: Mapped[list["DocumentExtraction"]] = relationship(back_populates="parcel")
    validation_records: Mapped[list["ValidationRecord"]] = relationship(back_populates="parcel")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="preliminary")  # preliminary|final
    ref_no: Mapped[str] = mapped_column(String(60))
    issued_on: Mapped[date] = mapped_column(Date)
    area_ha: Mapped[float] = mapped_column(Float, default=0.0)


class Award(Base):
    __tablename__ = "awards"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    award_no: Mapped[str] = mapped_column(String(60))
    declared_on: Mapped[date] = mapped_column(Date)
    total_area_ha: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount_cr: Mapped[float] = mapped_column(Float, default=0.0)


class Compensation(Base):
    __tablename__ = "compensation"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parcel_id: Mapped[int] = mapped_column(ForeignKey("parcels.id", ondelete="CASCADE"), index=True)
    assessed_cr: Mapped[float] = mapped_column(Float)
    disbursed_cr: Mapped[float] = mapped_column(Float, default=0.0)
    assessed_on: Mapped[date] = mapped_column(Date)
    last_disbursed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parcel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parcels.id", ondelete="SET NULL"), nullable=True)
    head_name: Mapped[str] = mapped_column(String(120), default="")
    members: Mapped[int] = mapped_column(Integer, default=4)
    displaced: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending | package_approved | allotted | resettled
    rr_status: Mapped[str] = mapped_column(String(20), default="pending")


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (UniqueConstraint("project_id", "stage"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(30))
    planned_days: Mapped[int] = mapped_column(Integer)
    planned_start: Mapped[date] = mapped_column(Date)
    planned_end: Mapped[date] = mapped_column(Date)
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    project: Mapped[Project] = relationship(back_populates="milestones")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # stage_change | note | stalled | resumed | dispute | created | update
    kind: Mapped[str] = mapped_column(String(20))
    stage: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64), default="system")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    project: Mapped[Project] = relationship(back_populates="events")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="other")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version"
    )
    extractions: Mapped[list["DocumentExtraction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentExtraction.id.desc()"
    )
    validation_records: Mapped[list["ValidationRecord"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ValidationRecord.id.desc()"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(200))
    stored_path: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")
    document: Mapped[Document] = relationship(back_populates="versions")


class DocumentExtraction(Base):
    """Structured extraction results with field-level confidence (H01-H30, I01-I15)."""
    __tablename__ = "document_extractions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True)
    parcel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parcels.id", ondelete="SET NULL"), nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    validation_status: Mapped[str] = mapped_column(String(20), default="passed")  # passed|warning|failed
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    fields_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped["Document"] = relationship(back_populates="extractions")
    parcel: Mapped[Optional["Parcel"]] = relationship(back_populates="extractions")
    validation_records: Mapped[list["ValidationRecord"]] = relationship(back_populates="extraction", cascade="all, delete-orphan")


class ValidationRecord(Base):
    """Business rule validation results and error logs (J01-J30)."""
    __tablename__ = "validation_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("document_extractions.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    parcel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parcels.id", ondelete="CASCADE"), nullable=True, index=True)
    rule_id: Mapped[str] = mapped_column(String(32), index=True)  # J01 - J30
    rule_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # passed|warning|failed
    severity: Mapped[str] = mapped_column(String(20))  # low|medium|high|critical
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    extraction: Mapped[Optional["DocumentExtraction"]] = relationship(back_populates="validation_records")
    document: Mapped[Optional["Document"]] = relationship(back_populates="validation_records")
    parcel: Mapped[Optional["Parcel"]] = relationship(back_populates="validation_records")


class AuditLog(Base):
    """Append-only, hash-chained. Each row commits to the one before it."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10))  # low|medium|high|critical
    message: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="rule")  # rule|model
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class RiskScore(Base):
    __tablename__ = "risk_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stage: Mapped[str] = mapped_column(String(30))
    probability: Mapped[float] = mapped_column(Float)
    score: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(10))  # low|medium|high|critical
    model_version: Mapped[str] = mapped_column(String(30))
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    drivers: Mapped[list] = mapped_column(JSON, default=list)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    stage_profile: Mapped[list] = mapped_column(JSON, default=list)
    # Filled when the stage finishes: 1 = the stage ran > 25% over plan. Feeds retraining.
    label: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    labelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(30), unique=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    n_train: Mapped[int] = mapped_column(Integer, default=0)
    n_real: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
