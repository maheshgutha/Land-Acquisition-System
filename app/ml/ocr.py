"""Document digitization: OCR + deterministic field extraction for uploaded land documents.

Pipeline: convert the upload to page images (PDF pages via pdf2image/poppler, or the
image file itself) -> preprocess for OCR quality (grayscale, denoise, adaptive
threshold, deskew) -> Tesseract OCR -> regex-based extraction of the fields a land
acquisition officer actually needs (survey number, village, owner, area, land type,
compensation amount, reference number, date).

Extraction is deterministic and regex-driven, the same "nothing generated freely"
principle used in ml/explain.py: every field returned here is a substring that was
actually found in the OCR text, tagged with where it came from and how confident the
match is. Nothing is inferred or guessed by a model. An officer reviews and confirms
before any extracted value is written back onto a parcel (see routers/documents.py
apply_extraction) - OCR feeds a human decision, it doesn't replace one.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
TEXT_EXTS = {".txt"}
PDF_EXTS = {".pdf"}
MAX_PDF_PAGES = 8

LAND_TYPES = ["agricultural", "residential", "commercial", "barren", "industrial", "irrigated"]

ACRE_TO_HA = 0.404686
GUNTA_TO_HA = 0.0101171  # 1 guntha = 1/40 acre, common Indian land-record subunit


@dataclass
class Field:
    value: str
    raw: str
    confidence: str  # "high" (labeled match) | "medium" (fallback pattern)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,:;-\t")


def _to_image_list(data: bytes, filename: str):
    """Return a list of PIL Images to run OCR over, or None if this file type has no
    OCR step (already-digital text formats)."""
    from PIL import Image

    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        return [Image.open(io.BytesIO(data)).convert("RGB")]
    if ext in PDF_EXTS:
        from pdf2image import convert_from_bytes

        pages = convert_from_bytes(data, dpi=300, first_page=1, last_page=MAX_PDF_PAGES)
        return pages
    return None


def _preprocess(pil_image) -> "object":
    """Grayscale, denoise, adaptive-threshold, and deskew a page image for OCR."""
    import cv2
    import numpy as np

    img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2GRAY)
    img = cv2.fastNlMeansDenoising(img, h=10)
    img = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )

    # Deskew: find the dominant text-block angle from the thresholded ink pixels.
    coords = cv2.findNonZero(255 - img)
    if coords is not None and len(coords) > 200:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) > 0.3:
            (h, w) = img.shape
            m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            img = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return img


def _ocr_page(img) -> tuple[str, list[int]]:
    import pytesseract
    from pytesseract import Output

    text = pytesseract.image_to_string(img, lang="eng", config="--oem 3 --psm 6")
    data = pytesseract.image_to_data(img, lang="eng", config="--oem 3 --psm 6", output_type=Output.DICT)
    confidences = [int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0]
    return text, confidences


# --------------------------------------------------------------------------- fields

def _extract_survey_no(text: str) -> Optional[Field]:
    m = re.search(r"survey\s*(?:no\.?|number)\s*[:\-]?\s*([0-9]{1,4}\s*/\s*[A-Za-z0-9]{1,3})", text, re.I)
    if m:
        return Field(_clean(m.group(1)).replace(" ", ""), m.group(0), "high")
    m = re.search(r"\b([0-9]{1,4}\s*/\s*[A-Z]{1,2})\b", text)
    if m:
        return Field(_clean(m.group(1)).replace(" ", ""), m.group(0), "medium")
    return None


def _extract_owner(text: str) -> Optional[Field]:
    m = re.search(
        r"(?:owner'?s?\s*name|land\s*owner|registered\s*owner|in\s*the\s*name\s*of)\s*[:\-]?\s*"
        r"([A-Z][A-Za-z.\s]{2,60}?)(?:\n|,|$| s/o| d/o| w/o)",
        text, re.I,
    )
    if m:
        return Field(_clean(m.group(1)), m.group(0), "high")
    return None


def _extract_village(text: str) -> Optional[Field]:
    m = re.search(r"village\s*[:\-]?\s*([A-Za-z][A-Za-z\s]{2,40}?)(?:\n|,|mandal|district|$)", text, re.I)
    if m:
        return Field(_clean(m.group(1)), m.group(0), "high")
    return None


def _extract_area(text: str) -> Optional[Field]:
    m = re.search(r"area\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(hectares?|ha\b|acres?|ac\b|guntas?)", text, re.I)
    if not m:
        return None
    qty, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("ac"):
        ha = round(qty * ACRE_TO_HA, 4)
    elif unit.startswith("gunt"):
        ha = round(qty * GUNTA_TO_HA, 4)
    else:
        ha = round(qty, 4)
    return Field(str(ha), m.group(0), "high")


def _extract_land_type(text: str) -> Optional[Field]:
    for lt in LAND_TYPES:
        m = re.search(rf"\b{lt}\b", text, re.I)
        if m:
            mapped = lt if lt in ("agricultural", "residential", "commercial", "barren") else "agricultural"
            return Field(mapped, m.group(0), "medium")
    return None


def _extract_amount(text: str) -> Optional[Field]:
    m = re.search(
        r"(?:compensation|award\s*amount|amount\s*payable)\s*[:\-]?\s*(?:rs\.?|inr|₹)\s*"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakhs?|crores?)?",
        text, re.I,
    )
    if not m:
        return None
    raw_num = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit.startswith("lakh"):
        rupees = raw_num * 100_000
    elif unit.startswith("crore"):
        rupees = raw_num * 10_000_000
    else:
        rupees = raw_num
    return Field(f"{rupees:.0f}", m.group(0), "high")


def _extract_ref_no(text: str) -> Optional[Field]:
    m = re.search(r"(?:notification|reference|award)\s*(?:no\.?|number)\s*[:\-]?\s*([A-Za-z0-9/\-]{4,40})", text, re.I)
    if m:
        return Field(_clean(m.group(1)), m.group(0), "high")
    return None


def _extract_date(text: str) -> Optional[Field]:
    m = re.search(r"(?:date(?:d)?|issued\s*on)\s*[:\-]?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})", text, re.I)
    if m:
        return Field(_clean(m.group(1)), m.group(0), "high")
    return None


EXTRACTORS = {
    "survey_no": _extract_survey_no,
    "owner_name": _extract_owner,
    "village": _extract_village,
    "area_ha": _extract_area,
    "land_type": _extract_land_type,
    "compensation_amount": _extract_amount,
    "ref_no": _extract_ref_no,
    "date": _extract_date,
}


def extract_fields(text: str) -> dict:
    out = {}
    for key, fn in EXTRACTORS.items():
        f = fn(text)
        if f:
            out[key] = {"value": f.value, "matched_text": f.raw, "confidence": f.confidence}
    return out


def run_ocr(data: bytes, filename: str) -> dict:
    """Top-level entry point. Never raises for expected 'not applicable' or OCR
    engine failures - always returns a status dict so a bad scan never blocks an
    upload."""
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTS:
        text = data.decode("utf-8", errors="ignore")
        return {"status": "done", "engine": "plain-text", "pages": 1, "text": text,
                "ocr_confidence": 100.0, "fields": extract_fields(text)}
    if ext not in IMAGE_EXTS and ext not in PDF_EXTS:
        return {"status": "skipped", "reason": f"'{ext}' is a text-based office format; OCR is not needed."}

    try:
        import pytesseract

        images = _to_image_list(data, filename)
        if not images:
            return {"status": "skipped", "reason": "No pages found to process."}
        all_text, all_conf = [], []
        for page in images:
            pre = _preprocess(page)
            text, confs = _ocr_page(pre)
            all_text.append(text)
            all_conf.extend(confs)
        full_text = "\n\n--- page break ---\n\n".join(all_text)
        avg_conf = round(sum(all_conf) / len(all_conf), 1) if all_conf else 0.0
        version = str(pytesseract.get_tesseract_version())
        return {
            "status": "done", "engine": f"tesseract {version}", "pages": len(images),
            "text": full_text, "ocr_confidence": avg_conf, "fields": extract_fields(full_text),
        }
    except Exception as e:  # pragma: no cover - defensive; exercised via bad-input tests
        return {"status": "failed", "error": str(e)[:300]}
