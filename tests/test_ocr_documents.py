"""Tests for the OCR + field-extraction pipeline (app/ml/ocr.py) and the documents
router endpoints that expose it. Covers: the pure-text regex extractor (fast, no
image rendering), the full OCR pipeline against a synthetically rendered scanned
image AND a scanned PDF (the two real input types field officers upload), and the
end-to-end API flow: upload -> OCR runs automatically -> an officer reviews and
applies the extracted fields onto the matching parcel, fully audited.
"""
import io

import cv2
import numpy as np
import pytest
from PIL import Image

from app.ml import ocr


SAMPLE_TEXT = """
LAND ACQUISITION - AWARD DOCUMENT
SURVEY NO: 245/B
VILLAGE: KOTHAPALLI
OWNER NAME: RAMESH NAIDU
AREA: 2.5 HECTARES
LAND TYPE: AGRICULTURAL
COMPENSATION: RS. 1250000
NOTIFICATION NO: NHAI/2025/AP/0456
DATE: 12/03/2025
"""


def test_extract_fields_pure_text():
    """The regex extractor itself, with no OCR involved - fast and exact."""
    fields = ocr.extract_fields(SAMPLE_TEXT)
    assert fields["survey_no"]["value"] == "245/B"
    assert fields["survey_no"]["confidence"] == "high"
    assert fields["village"]["value"] == "KOTHAPALLI"
    assert fields["owner_name"]["value"] == "RAMESH NAIDU"
    assert fields["area_ha"]["value"] == "2.5"
    assert fields["land_type"]["value"] == "agricultural"
    assert fields["compensation_amount"]["value"] == "1250000"
    assert fields["ref_no"]["value"] == "NHAI/2025/AP/0456"
    assert fields["date"]["value"] == "12/03/2025"


def test_extract_fields_converts_units_and_lakh_crore():
    text = "Survey Number: 12/A\nArea : 5 acres\nCompensation: Rs 15 lakh"
    fields = ocr.extract_fields(text)
    assert fields["area_ha"]["value"] == str(round(5 * ocr.ACRE_TO_HA, 4))
    assert fields["compensation_amount"]["value"] == "1500000"


def _render_sample_image() -> bytes:
    """Render SAMPLE_TEXT onto a clean white canvas as a real scanned-document
    stand-in, using cv2.putText for crisp, OCR-friendly synthetic text."""
    canvas = np.full((520, 900), 255, dtype=np.uint8)
    y = 60
    for line in SAMPLE_TEXT.strip().splitlines():
        cv2.putText(canvas, line, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,), 2, cv2.LINE_AA)
        y += 55
    ok, buf = cv2.imencode(".png", canvas)
    assert ok
    return buf.tobytes()


@pytest.fixture(scope="module")
def sample_scan_png() -> bytes:
    return _render_sample_image()


@pytest.fixture(scope="module")
def sample_scan_pdf(sample_scan_png) -> bytes:
    """A genuine one-page PDF containing the same rendered scan, so the PDF branch
    (pdf2image -> poppler -> tesseract) is exercised, not just raw images."""
    img = Image.open(io.BytesIO(sample_scan_png)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PDF")
    return out.getvalue()


def test_ocr_pipeline_on_scanned_image(sample_scan_png):
    result = ocr.run_ocr(sample_scan_png, "award_scan.png")
    assert result["status"] == "done", result
    assert result["engine"].startswith("tesseract")
    assert result["ocr_confidence"] > 50, result["text"]
    fields = result["fields"]
    assert fields["survey_no"]["value"] == "245/B", result["text"]
    assert fields["land_type"]["value"] == "agricultural"
    assert fields["compensation_amount"]["value"] == "1250000"


def test_ocr_pipeline_on_scanned_pdf(sample_scan_pdf):
    result = ocr.run_ocr(sample_scan_pdf, "award_scan.pdf")
    assert result["status"] == "done", result
    assert result["pages"] == 1
    assert result["fields"]["survey_no"]["value"] == "245/B", result["text"]


def test_ocr_skips_text_based_office_formats():
    result = ocr.run_ocr(b"whatever", "notes.docx")
    assert result["status"] == "skipped"
    assert "not needed" in result["reason"]


def test_ocr_never_raises_on_garbage_pdf_bytes():
    """A corrupt/incomplete PDF must fail soft (status: failed) - it must never
    take the surrounding upload request down with it."""
    result = ocr.run_ocr(b"%PDF-1.4 not a real pdf", "broken.pdf")
    assert result["status"] == "failed"
    assert result["error"]


# --------------------------------------------------------------------- API flow

def test_upload_runs_ocr_and_officer_applies_it_to_a_parcel(client, auth, sample_scan_pdf):
    h_field = auth("field_krishna")
    pid = client.get("/api/projects", headers=h_field).json()["items"][0]["id"]
    parcels = client.get(f"/api/projects/{pid}/parcels", headers=h_field).json()
    parcel = parcels[0]

    # Re-render a scan that names *this* project's actual parcel so apply-extraction
    # has a real survey number to match against.
    text = (
        f"LAND ACQUISITION - AWARD DOCUMENT\n"
        f"SURVEY NO: {parcel['survey_no']}\n"
        f"VILLAGE: SETTIBALIJAPALEM\n"
        f"OWNER NAME: KOTESWARA RAO\n"
        f"AREA: 1.8 HECTARES\n"
        f"LAND TYPE: RESIDENTIAL\n"
        f"COMPENSATION: RS. 900000\n"
    )
    canvas = np.full((420, 900), 255, dtype=np.uint8)
    y = 60
    for line in text.strip().splitlines():
        cv2.putText(canvas, line, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,), 2, cv2.LINE_AA)
        y += 55
    ok, buf = cv2.imencode(".png", canvas)
    assert ok

    r = client.post(
        f"/api/projects/{pid}/documents", headers=h_field,
        files={"file": ("award_scan.png", buf.tobytes(), "image/png")},
        data={"category": "award_copy", "name": "Award scan for OCR test"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    version_id = body["versions"][-1]["id"]
    assert body["latest_extraction"]["status"] == "done"
    assert body["latest_extraction"]["fields"]["survey_no"]["value"] == parcel["survey_no"]

    got = client.get(f"/api/documents/versions/{version_id}/extraction", headers=h_field)
    assert got.status_code == 200
    fields = got.json()["fields"]

    # A field officer can OCR a document, but only district/state/central review-and-apply it.
    denied = client.post(
        f"/api/documents/versions/{version_id}/apply-extraction", headers=h_field,
        json={"fields": {"survey_no": parcel["survey_no"], "owner_name": fields["owner_name"]["value"]}},
    )
    assert denied.status_code == 403

    applied = client.post(
        f"/api/documents/versions/{version_id}/apply-extraction", headers=auth("dist_krishna"),
        json={"fields": {
            "survey_no": parcel["survey_no"],
            "owner_name": fields["owner_name"]["value"],
            "area_ha": fields["area_ha"]["value"],
            "land_type": fields["land_type"]["value"],
        }},
    )
    assert applied.status_code == 200, applied.text
    updated = applied.json()["updated_fields"]
    assert updated["owner_name"] == "KOTESWARA RAO"
    assert updated["land_type"] == "residential"

    check = client.get(f"/api/projects/{pid}/parcels", headers=h_field).json()
    changed = next(p for p in check if p["id"] == parcel["id"])
    assert changed["owner_name"] == "KOTESWARA RAO"

    log = client.get("/api/audit", params={"action": "document_extraction_applied"}, headers=auth("auditor")).json()
    assert log["total"] >= 1
