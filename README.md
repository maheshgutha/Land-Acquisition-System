# LandScan - Land Acquisition & Management System (SIH 2026, PS 26016 prototype)

FastAPI + SQLAlchemy backend, LightGBM/TreeSHAP delay-risk model, vanilla JS + Leaflet front end (no build step).

    pip install -r requirements.txt
    LANDSCAN_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))") uvicorn app.main:app --port 8000
    # open http://localhost:8000  (first start seeds synthetic demo data; accounts listed on the login page, password demo1234)

    pytest                              # 36 tests: RBAC, stage gates, audit chain, documents, risk, reports
    python -m app.seed --reset          # rebuild the demo database
    docker compose up --build           # alternative

API docs: http://localhost:8000/docs. Config (env): LANDSCAN_DB, LANDSCAN_SECRET, LANDSCAN_STORAGE, LANDSCAN_MODELS,
LANDSCAN_AUTOSEED, LANDSCAN_DEMO. Set AUTOSEED=0 and DEMO=0 for anything that is not a demo.

All demo data is synthetic, integrations in `app/integrations.py` are mocks, and the base risk model is trained on
synthetic data until real stage outcomes are recorded (see `app/ml/datagen.py`).
"# Land-Acquisition-System" 
