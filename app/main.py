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
