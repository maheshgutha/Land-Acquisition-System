"""Tests for the OCR + field-extraction pipeline (app/ml/ocr.py) and the documents
router endpoints that expose it. Covers: the pure-text regex extractor (fast, no
image rendering), the full OCR pipeline against a synthetically rendered scanned
image AND a scanned PDF (the two real input types field officers upload), and the
end-to-end API flow: upload -> OCR runs automatically -> an officer reviews and
applies the extracted fields onto the matching parcel, fully audited.
"""
import importlib.util
import io
import shutil

import cv2
import numpy as np
import pytest
from PIL import Image

from app.ml import ocr

HAS_TESSERACT = bool(shutil.which("tesseract")) and importlib.util.find_spec("pytesseract") is not None
HAS_POPPLER = bool(shutil.which("pdftoppm"))
needs_tesseract = pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract OCR engine not installed")
needs_poppler = pytest.mark.skipif(not (HAS_TESSERACT and HAS_POPPLER), reason="Tesseract and poppler not installed")


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


@needs_tesseract
def test_ocr_pipeline_on_scanned_image(sample_scan_png):
    result = ocr.run_ocr(sample_scan_png, "award_scan.png")
    assert result["status"] == "done", result
    assert result["engine"].startswith("tesseract")
    assert result["ocr_confidence"] > 50, result["text"]
    fields = result["fields"]
    assert fields["survey_no"]["value"] == "245/B", result["text"]
    assert fields["land_type"]["value"] == "agricultural"
    assert fields["compensation_amount"]["value"] == "1250000"


@needs_poppler
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

@needs_tesseract
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


# ------------------------------------------------------- real-world wording & robustness

ROR_TEXT = """FORM - 1B  (Record of Rights)
Sy. No. 245/B
Village Kothapalli, Mandal Tadepalli
Pattadar: Ramesh Naidu S/o Venkata Rao
Extent: Ac. 2.50 Cts
Classification: Wet land
Total compensation payable: Rs.12,50,000/-
Proceedings No. B/456/2025 Dt. 12-03-2025
"""


def test_extract_fields_indian_land_record_wording():
    """Record-of-Rights style labels (Sy. No., Pattadar, Extent Ac./Cts, Dt., Rs. x/-)."""
    f = ocr.extract_fields(ROR_TEXT)
    assert f["survey_no"]["value"] == "245/B"
    assert f["village"]["value"] == "Kothapalli"
    assert f["owner_name"]["value"] == "Ramesh Naidu"
    assert f["area_ha"]["value"] == str(round(2.5 * ocr.ACRE_TO_HA, 4))
    assert f["land_type"]["value"] == "agricultural"
    assert f["compensation_amount"]["value"] == "1250000"
    assert f["ref_no"]["value"] == "B/456/2025"
    assert f["date"]["value"] == "12/03/2025"


def test_extract_area_mixed_units_and_text_dates():
    f = ocr.extract_fields("Extent: 3 Acres 20 Guntas\nDated 5th March 2025\nAward amount: Rs. 18.5 lakh")
    assert f["area_ha"]["value"] == str(round(3 * ocr.ACRE_TO_HA + 20 * ocr.GUNTA_TO_HA, 4))
    assert f["date"]["value"] == "05/03/2025"
    assert f["compensation_amount"]["value"] == "1850000"


def test_unlabelled_numbers_are_not_guessed_as_survey_numbers():
    """A reference like NHAI/2025/AP must never be passed off as a survey number."""
    f = ocr.extract_fields("NOTIFICATION NO: NHAI/2025/AP/0456\nVILLAGE: KOTHAPALLI")
    assert "survey_no" not in f


def test_impossible_dates_are_flagged_for_review():
    f = ocr.extract_fields("Dated: 42/03/2025")
    assert f["date"]["confidence"] == "medium"


def test_industrial_is_not_silently_mapped_to_agricultural():
    assert "land_type" not in ocr.extract_fields("Land Type: Industrial")


def _render_text(text: str, tilt: float = 0.0) -> bytes:
    canvas = np.full((520, 1000), 255, dtype=np.uint8)
    y = 50
    for line in text.strip().splitlines():
        cv2.putText(canvas, line, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,), 2, cv2.LINE_AA)
        y += 55
    if tilt:
        m = cv2.getRotationMatrix2D((500, 260), tilt, 1.0)
        canvas = cv2.warpAffine(canvas, m, (1000, 520), borderValue=255)
    ok, buf = cv2.imencode(".png", canvas)
    assert ok
    return buf.tobytes()


def test_deskew_estimate_is_correct_in_both_directions():
    """Regression: the old minAreaRect deskew doubled the tilt (and turned upright pages 90 deg on
    OpenCV 4.x). The estimate must undo the tilt, whatever the OpenCV version."""
    for tilt in (3.0, -3.0, 0.0):
        img = cv2.imdecode(np.frombuffer(_render_text(SAMPLE_TEXT, tilt), np.uint8), cv2.IMREAD_GRAYSCALE)
        angle = ocr._estimate_skew(img)
        assert abs(angle + tilt) <= 0.5, (tilt, angle)


@needs_tesseract
def test_ocr_reads_a_tilted_scan_correctly():
    result = ocr.run_ocr(_render_text(SAMPLE_TEXT, tilt=3.0), "tilted.png")
    f = result["fields"]
    assert f["survey_no"]["value"] == "245/B", result["text"]
    assert f["compensation_amount"]["value"] == "1250000", result["text"]
    assert f["date"]["value"] == "12/03/2025", result["text"]
    assert any("tilted" in w for w in result["warnings"])


@needs_poppler
def test_digital_pdf_uses_its_text_layer():
    lines = ["Sy. No. 112/2A", "Village: Rampur", "Pattadar: Sita Devi", "Extent: 3 Acres 20 Guntas"]
    content = "BT /F1 12 Tf 72 760 Td 16 TL " + " ".join(f"({x}) Tj T*" for x in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(pdf)
    pdf += (f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
            + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF")
    result = ocr.run_ocr(pdf.encode(), "digital.pdf")
    assert result["engine"] == "pdf text layer"
    assert result["fields"]["survey_no"]["value"] == "112/2A"
    assert result["fields"]["owner_name"]["value"] == "Sita Devi"


# ------------------------------------------------------------ digitization page API

@needs_tesseract
def test_sample_scan_round_trip_and_recent_list(client, auth):
    """The 'Try a sample scan' button: sample -> upload -> fields match the parcel -> listed."""
    h_field = auth("field_krishna")
    pid = client.get("/api/projects", headers=h_field).json()["items"][1]["id"]
    parcel = client.get(f"/api/projects/{pid}/parcels", headers=h_field).json()[0]
    scan = client.get(f"/api/projects/{pid}/sample-scan", headers=h_field)
    assert scan.status_code == 200 and scan.headers["content-type"] == "image/png"

    r = client.post(f"/api/projects/{pid}/documents", headers=h_field,
                    files={"file": ("sample_award.png", scan.content, "image/png")},
                    data={"category": "award_copy", "name": "Sample award scan"})
    assert r.status_code == 201, r.text
    ext = r.json()["latest_extraction"]
    assert ext["status"] == "done"
    assert ext["fields"]["survey_no"]["value"] == parcel["survey_no"].replace(" ", "").upper(), ext["text"]
    assert ext["fields_found"] >= 6, ext["text"]

    recent = client.get("/api/documents/extractions", headers=h_field).json()["items"]
    assert recent[0]["version_id"] == ext["document_version_id"]
    assert recent[0]["project_id"] == pid

    docs = client.get(f"/api/projects/{pid}/documents", headers=h_field).json()
    latest = next(d for d in docs if d["name"] == "Sample award scan")["versions"][-1]
    assert latest["ocr"]["status"] == "done"

    # Out-of-scope users never see it.
    other = client.get("/api/documents/extractions", headers=auth("dist_kurnool")).json()["items"]
    assert all(i["project_id"] != pid for i in other)


def test_apply_extraction_validates_values(client, auth):
    h_field = auth("field_krishna")
    pid = client.get("/api/projects", headers=h_field).json()["items"][2]["id"]
    parcel = client.get(f"/api/projects/{pid}/parcels", headers=h_field).json()[0]
    r = client.post(f"/api/projects/{pid}/documents", headers=h_field,
                    files={"file": ("award.txt", f"Survey No: {parcel['survey_no']}\n".encode(), "text/plain")},
                    data={"category": "award_copy", "name": "Validation check"})
    vid = r.json()["versions"][-1]["id"]
    h_dist = auth("dist_krishna")
    bad_type = client.post(f"/api/documents/versions/{vid}/apply-extraction", headers=h_dist,
                           json={"fields": {"survey_no": parcel["survey_no"], "land_type": "industrial"}})
    assert bad_type.status_code == 422
    bad_area = client.post(f"/api/documents/versions/{vid}/apply-extraction", headers=h_dist,
                           json={"fields": {"survey_no": parcel["survey_no"], "area_ha": "-4"}})
    assert bad_area.status_code == 422
    # Survey numbers match regardless of case/spacing differences from OCR.
    ok = client.post(f"/api/documents/versions/{vid}/apply-extraction", headers=h_dist,
                     json={"fields": {"survey_no": " " + parcel["survey_no"].lower() + " ", "village": "Rampur"}})
    assert ok.status_code == 200, ok.text


def test_heading_words_are_not_read_as_reference_labels():
    """Regression: 'AWARD NOTICE' used to match 'Award No.' and hide the real reference number."""
    f = ocr.extract_fields("SAMPLE AWARD NOTICE\nAward No: AWD/LA-AP-2025-0005/0031")
    assert f["ref_no"]["value"] == "AWD/LA-AP-2025-0005/0031"


def test_national_roles_see_every_digitized_document(client, auth):
    h_field = auth("field_krishna")
    pid = client.get("/api/projects", headers=h_field).json()["items"][0]["id"]
    r = client.post(f"/api/projects/{pid}/documents", headers=h_field,
                    files={"file": ("note.txt", b"Survey No: 1/A\n", "text/plain")},
                    data={"category": "other", "name": "Scope check"})
    vid = r.json()["versions"][-1]["id"]
    for user in ("auditor", "central"):
        items = client.get("/api/documents/extractions", headers=auth(user)).json()["items"]
        assert any(i["version_id"] == vid for i in items), user


def test_amount_survives_ocr_spaces_in_digit_groups():
    """Regression: OCR read 'Rs. 84, 28,000/-' and the amount came out as Rs 84."""
    f = ocr.extract_fields("Compensation: Rs. 84, 28,000/-")
    assert f["compensation_amount"]["value"] == "8428000"
    assert f["compensation_amount"]["confidence"] == "high"
    assert ocr.extract_fields("Compensation: Rs. 84")["compensation_amount"]["confidence"] == "medium"
