"""Application entry point: `uvicorn app.main:app`."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .config import AUTOSEED, SECRET_KEY
from .db import Base, SessionLocal, engine
from .models import Project
from .routers import analytics, auth, documents, land, projects, risk, system

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if SECRET_KEY == "dev-only-change-me":
        logging.getLogger("uvicorn.error").warning("LANDSCAN_SECRET is not set; using the development signing key. Set it before deploying.")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        try:
            cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(parcels)").fetchall()]
            if cols:
                if "validation_status" not in cols:
                    conn.exec_driver_sql("ALTER TABLE parcels ADD COLUMN validation_status VARCHAR(20) DEFAULT 'verified'")
                if "area_mismatch_pct" not in cols:
                    conn.exec_driver_sql("ALTER TABLE parcels ADD COLUMN area_mismatch_pct FLOAT DEFAULT 0.0")
                if "doc_area_ha" not in cols:
                    conn.exec_driver_sql("ALTER TABLE parcels ADD COLUMN doc_area_ha FLOAT DEFAULT NULL")
                conn.commit()
        except Exception:
            pass
    with SessionLocal() as db:
        empty = db.scalar(select(func.count()).select_from(Project)) == 0
        if empty and AUTOSEED:
            from .seed import seed  # imported lazily: seeding pulls in the ML stack

            seed(db)
        else:
            from .services import risk_service

            risk_service.active_model(db)  # load (or bootstrap) the model once at start-up
            db.commit()
    yield


app = FastAPI(
    title="LandScan - Land Acquisition & Management System",
    version="0.1.0",
    description=(
        "Prototype for SIH 2026 PS 26016. All data in the demo database is synthetic; integrations are mocks "
        "behind a gateway; the delay-risk model is trained on synthetic data until real outcomes are recorded."
    ),
    lifespan=lifespan,
)

for module in (auth, projects, land, documents, analytics, risk, system):
    app.include_router(module.router)

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
