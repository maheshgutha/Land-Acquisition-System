# LandScan AI - Land Acquisition & Management System (SIH 2026, PS 26016 prototype)

FastAPI + SQLAlchemy backend, LightGBM/TreeSHAP delay-risk model, Tesseract-based document digitization,
vanilla JS + Leaflet front end (no build step).

System packages needed for OCR (already baked into the Docker image; install locally if not using Docker):
`tesseract-ocr` and `poppler-utils` (Debian/Ubuntu: `apt-get install tesseract-ocr poppler-utils`;
Windows: `winget install UB-Mannheim.TesseractOCR oschwartz10612.Poppler`, then add both to PATH).

    pip install -r requirements.txt
    LANDSCAN_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))") uvicorn app.main:app --port 8000
    # open http://localhost:8000  (first start seeds synthetic demo data; accounts listed on the login page, password demo1234)

    pytest                              # 57 tests: RBAC, stage gates, audit chain, documents, OCR digitization, risk, reports
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

**Where it is in the app:** the **Digitize** tab in the top navigation (also linked from the dashboard's
Documents card and from each project's Documents tab). Pick a project, upload a scan - or press
**Try a sample scan** for a synthetic award notice matching one of that project's parcels - and the extracted
fields appear on the right. District, state and central officers review them there and press **Apply to parcel**.

Every PDF or image upload, from either place, runs through `app/ml/ocr.py`:

1. **Digital PDFs** use their own text layer (`pdftotext`) - exact, no OCR needed.
2. **Scans and photos** are rendered, rescaled so text is a consistent size, straightened (projection-profile
   skew search, independent of the OpenCV version), lighting-flattened, denoised and read with Tesseract.
   A low-confidence read is retried with stronger denoising (phone photos).
3. A deterministic, label-anchored regex layer extracts survey number, owner, village, area, land type,
   compensation, reference number and date. It understands the wording of Indian land records - *Sy. No.*,
   *Khasra No.*, *Pattadar*, *Khatedar*, *Extent: Ac. 2.50 Cts*, *3 Acres 20 Guntas*, *cents*, *Rs. 12,50,000/-*,
   *18.5 lakh*, *Dt. 12-03-2025*, *5th March 2025* - and converts areas to hectares.

Nothing is guessed: a survey number is only taken from next to its label, impossible dates and any field with a
low-confidence OCR word are marked **needs review**, and warnings (low scan quality, tilt corrected, no survey
number) are shown to the reviewer. Values are never written onto a parcel automatically; the officer confirms
them, the server validates them (land type, area range, survey-number match), and both steps are audited
(`document_ocr_processed`, `document_extraction_applied`).

Limits: English text only by default. For Hindi/Telugu/etc. records install the Tesseract language packs and set
`LANDSCAN_OCR_LANGS`, e.g. `eng+hin+tel`. Handwritten records are not supported.

API: `GET /api/documents/extractions` (recent, role-scoped), `GET /api/documents/versions/{id}/extraction`,
`POST /api/documents/versions/{id}/apply-extraction`, `GET /api/projects/{id}/sample-scan`.
Tests: `tests/test_ocr_documents.py` (OCR tests skip automatically when Tesseract/poppler are not installed).
