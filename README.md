# LandScan - Land Acquisition & Management System (SIH 2026, PS 26016 prototype)

FastAPI + SQLAlchemy backend, LightGBM/TreeSHAP delay-risk model, Tesseract-based document digitization,
vanilla JS + Leaflet front end (no build step).

System packages needed for OCR (already baked into the Docker image; install locally if not using Docker):
`tesseract-ocr` and `poppler-utils` (Debian/Ubuntu: `apt-get install tesseract-ocr poppler-utils`).

    pip install -r requirements.txt
    LANDSCAN_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))") uvicorn app.main:app --port 8000
    # open http://localhost:8000  (first start seeds synthetic demo data; accounts listed on the login page, password demo1234)

    pytest                              # 45 tests: RBAC, stage gates, audit chain, documents, OCR digitization, risk, reports
    python -m app.seed --reset          # rebuild the demo database
    docker compose up --build           # alternative

Optional real-browser end-to-end test (skips cleanly if not set up; never required for the 37 above):

    pip install -r requirements-dev.txt && playwright install chromium
    pytest tests/test_e2e_smoke.py -q

API docs: http://localhost:8000/docs. Config (env): LANDSCAN_DB, LANDSCAN_SECRET, LANDSCAN_STORAGE, LANDSCAN_MODELS,
LANDSCAN_AUTOSEED, LANDSCAN_DEMO. Set AUTOSEED=0 and DEMO=0 for anything that is not a demo.

All demo data is synthetic, integrations in `app/integrations.py` are mocks, and the base risk model is trained on
synthetic data until real stage outcomes are recorded (see `app/ml/datagen.py`).

## Document digitization (OCR)

Every upload (PDF or image) is run through `app/ml/ocr.py` automatically: pages are deskewed and denoised with
OpenCV, read with Tesseract, and a deterministic regex layer pulls out survey number, owner, village, area,
land type, compensation amount, reference number and date - each tagged with a confidence level, exactly like
the risk model's SHAP drivers ("nothing generated freely" - see the docstring in `app/ml/ocr.py`). Results are
never written onto a parcel automatically: an officer (district/state/central) reviews the extracted fields in
the Documents tab, corrects anything OCR got wrong, and applies them explicitly - fully audited
(`document_ocr_processed`, `document_extraction_applied` in the audit log). See `app/routers/documents.py`
(`get_extraction`, `apply_extraction`) and `tests/test_ocr_documents.py` for the full flow.
