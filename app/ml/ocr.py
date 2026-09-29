"""Document digitization: OCR + deterministic field extraction for uploaded land documents.

Pipeline:
  1. Digital PDFs: use the PDF's own text layer (pdftotext) - exact, fast, no OCR needed.
  2. Scanned PDFs / images: render pages (pdf2image/poppler) -> normalise scale so text is a
     consistent size -> estimate and correct skew (projection-profile search) -> denoise +
     adaptive threshold -> Tesseract OCR with per-word confidences.
  3. Regex-based extraction of the fields a land acquisition officer needs (survey number,
     village, owner, area, land type, compensation, reference number, date). Every pattern
     is anchored on a label actually printed on the document, including the wording used in
     Indian land records (Sy. No., Pattadar, Khatedar, Extent Ac./Cts/Guntas, Dt., Rs. x/-).

Extraction is deterministic, the same "nothing generated freely" principle used in
ml/explain.py: every field returned is a substring that was actually found in the text,
tagged with the text it came from and a confidence. A field is only "high" confidence when
it was found next to its label AND every OCR word in it was read confidently; otherwise it
is "medium" (shown as "needs review"). Nothing is inferred by a model. An officer reviews
and confirms before any value is written onto a parcel (routers/documents.py
apply_extraction) - OCR feeds a human decision, it doesn't replace one.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
TEXT_EXTS = {".txt"}
PDF_EXTS = {".pdf"}
MAX_PDF_PAGES = 8
PDF_RENDER_DPI = 200

# Tesseract language packs to use, e.g. "eng+hin+tel" once those traineddata files are installed.
OCR_LANGS = os.getenv("LANDSCAN_OCR_LANGS", "eng")

# Target height, in pixels, of a typical character after scale normalisation. Tesseract is most
# accurate around 20-40 px; normalising first also keeps the fixed-size threshold window valid.
TARGET_CHAR_PX = 32
MAX_SKEW_DEG = 6.0
MIN_SKEW_DEG = 0.3
LOW_WORD_CONF = 60           # a word below this is treated as uncertain
MIN_TEXT_LAYER_CHARS = 40    # a PDF text layer shorter than this is treated as "scanned"

# Values a parcel's land_type may take (see integrations.LAND_TYPES / seed data).
PARCEL_LAND_TYPES = ("agricultural", "residential", "commercial", "barren")

ACRE_TO_HA = 0.404686
GUNTA_TO_HA = 0.0101171   # 1 guntha = 1/40 acre (Telangana, Karnataka, Maharashtra records)
CENT_TO_HA = 0.00404686   # 1 cent = 1/100 acre (Andhra Pradesh, Kerala, Tamil Nadu records)
SQM_TO_HA = 0.0001

ALL_FIELDS = ("survey_no", "owner_name", "village", "area_ha", "land_type", "compensation_amount", "ref_no", "date")


@dataclass
class Field:
    value: str
    raw: str
    confidence: str  # "high" (labelled match) | "medium" (needs review)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,:;-\t|")


# ============================================================================ image side

def _pdf_text_layer(data: bytes) -> Optional[str]:
    """Text embedded in a digital (non-scanned) PDF, or None if there isn't a usable one."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", "-l", str(MAX_PDF_PAGES), "-", "-"], input=data,
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.decode("utf-8", errors="ignore") if out.returncode == 0 else ""
    return text if len(re.sub(r"\s", "", text)) >= MIN_TEXT_LAYER_CHARS else None


def _to_image_list(data: bytes, filename: str):
    """Page images to OCR, or None if this file type has no OCR step."""
    from PIL import Image, ImageOps

    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        img = Image.open(io.BytesIO(data))
        frames = []
        for i in range(min(getattr(img, "n_frames", 1), MAX_PDF_PAGES)):  # multi-page TIFFs
            img.seek(i)
            frames.append(ImageOps.exif_transpose(img.copy()).convert("RGB"))  # honour phone-camera rotation
        return frames
    if ext in PDF_EXTS:
        from pdf2image import convert_from_bytes

        return convert_from_bytes(data, dpi=PDF_RENDER_DPI, first_page=1, last_page=MAX_PDF_PAGES)
    return None


def _normalise_scale(gray):
    """Resize so a typical character is ~TARGET_CHAR_PX tall, whatever the scan resolution."""
    import cv2
    import numpy as np

    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    h, w = stats[1:, cv2.CC_STAT_HEIGHT], stats[1:, cv2.CC_STAT_WIDTH]
    keep = (h >= 5) & (h < gray.shape[0] * 0.15) & (w < gray.shape[1] * 0.3) & (w >= 2)
    if keep.sum() < 15:
        return gray
    scale = float(np.clip(TARGET_CHAR_PX / np.median(h[keep]), 0.35, 3.0))
    if abs(scale - 1) < 0.15:
        return gray
    # Keep the result to a sane size either way.
    scale = min(scale, 6000 / max(gray.shape))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)


def _estimate_skew(gray) -> float:
    """Angle (degrees, OpenCV sign convention) that rotates the text lines horizontal.

    Projection-profile search: rotate a small binarised copy through candidate angles and
    keep the one whose row-sum profile is sharpest (text lines crisp, gaps empty). Unlike
    cv2.minAreaRect this does not depend on OpenCV's version-specific angle convention and is
    not thrown off by stamps, logos or margins."""
    import cv2
    import numpy as np

    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    f = 900 / max(bw.shape)
    if f < 1:
        bw = cv2.resize(bw, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    hgt, wid = bw.shape
    centre = (wid / 2, hgt / 2)

    def sharpness(a: float) -> float:
        m = cv2.getRotationMatrix2D(centre, a, 1.0)
        r = cv2.warpAffine(bw, m, (wid, hgt), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = r.sum(axis=1, dtype=np.float64)
        return float(np.square(np.diff(prof)).sum())

    coarse = max(np.arange(-MAX_SKEW_DEG, MAX_SKEW_DEG + 1e-6, 0.5), key=sharpness)
    fine = max(np.arange(coarse - 0.5, coarse + 0.5 + 1e-6, 0.1), key=sharpness)
    return float(round(fine, 2))


def _rotate(gray, angle: float):
    import cv2

    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _preprocess(pil_image):
    """Grayscale -> normalise scale -> deskew. Returns (grayscale page, skew angle corrected)."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2GRAY)
    gray = _normalise_scale(gray)
    angle = _estimate_skew(gray)
    if MIN_SKEW_DEG <= abs(angle) < MAX_SKEW_DEG:
        gray = _rotate(gray, angle)
    else:
        angle = 0.0
    return gray, angle


# Denoising strengths tried in order; the stronger pass only runs when the first reads poorly
# (phone photos: sensor noise, JPEG artefacts). Strong denoising on a clean scan thins fine print.
DENOISE_PASSES = (10, 22)
RETRY_BELOW_CONF = 80


def _binarise(gray, strength: int):
    """Flatten uneven lighting (divide by the blurred background), denoise, then Otsu threshold."""
    import cv2

    background = cv2.medianBlur(gray, 31)
    flat = cv2.divide(gray, background, scale=255)
    flat = cv2.fastNlMeansDenoising(flat, h=strength)
    _, bw = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def _ocr_best(gray) -> tuple[str, list[tuple[str, int]]]:
    best = None
    for strength in DENOISE_PASSES:
        text, words = _ocr_page(_binarise(gray, strength))
        conf = sum(c for _, c in words) / len(words) if words else 0
        if best is None or conf > best[0]:
            best = (conf, text, words)
        if conf >= RETRY_BELOW_CONF:
            break
    return best[1], best[2]


def _ocr_page(img) -> tuple[str, list[tuple[str, int]]]:
    """OCR text plus (word, confidence) for every recognised word."""
    import pytesseract
    from pytesseract import Output

    config = "--oem 3 --psm 6"
    text = pytesseract.image_to_string(img, lang=OCR_LANGS, config=config)
    data = pytesseract.image_to_data(img, lang=OCR_LANGS, config=config, output_type=Output.DICT)
    words = []
    for w, c in zip(data.get("text", []), data.get("conf", [])):
        try:
            conf = int(float(c))
        except (TypeError, ValueError):
            continue
        if conf >= 0 and str(w).strip():
            words.append((str(w).strip(), conf))
    return text, words


# ============================================================================ fields

SEP = r"\s*[:;\-–=]*\s*"                          # separator between a label and its value
END = r"(?=\s*(?:\n|,|;|\||\s{3,}|$))"             # end of a value: newline, comma, column gap
FLAGS = re.I | re.M

SURVEY_LABEL = (r"\b(?:survey|sy|syno|sur|r\s*\.?\s*s|re-?survey|khasra|gat|dag|plot)\s*\.?\s*"
                r"(?:no|nos|number)\b\s*\.?")


def _extract_survey_no(text: str) -> Optional[Field]:
    # Label required: an unlabelled "digits/letters" pattern also matches reference and file
    # numbers (e.g. "NHAI/2025/AP"), and a wrong survey number is worse than none.
    m = re.search(SURVEY_LABEL + SEP + r"([0-9]{1,5}(?:\s*[/\-]\s*[0-9A-Za-z]{1,4}){0,3})\b", text, FLAGS)
    if m:
        return Field(re.sub(r"\s+", "", m.group(1)).upper(), m.group(0), "high")
    return None


OWNER_LABEL = (r"(?:owner'?s?\s*name|name\s*of\s*(?:the\s*)?(?:land\s*)?(?:owner|pattadar|khatedar|holder)|"
               r"land\s*owner|registered\s*owner|pattadar(?:'?s?\s*name)?|khatedar|land\s*holder|landholder|"
               r"in\s*the\s*name\s*of|awardee)")


def _extract_owner(text: str) -> Optional[Field]:
    m = re.search(
        OWNER_LABEL + SEP + r"([A-Za-z][A-Za-z.' ]{2,60}?)"
        r"(?=\s*(?:\n|,|;|\(|\||\s{3,}|$|\s+(?:s|d|w)\s*/\s*o\b|\s+(?:son|daughter|wife)\s+of\b))",
        text, FLAGS,
    )
    if m:
        return Field(_clean(m.group(1)), m.group(0), "high")
    return None


def _extract_village(text: str) -> Optional[Field]:
    m = re.search(
        r"(?:revenue\s*village|village(?:\s*name)?|\bmou?za|\bmauza|\bgrama?m?)\b" + SEP +
        r"([A-Za-z][A-Za-z ]{1,40}?)(?=\s*(?:\n|,|;|\||\s{3,}|$|mandal|taluk|tehsil|tahsil|district|block|\bdist\b))",
        text, FLAGS,
    )
    if m:
        return Field(_clean(m.group(1)), m.group(0), "high")
    return None


_NUM = r"([0-9]+(?:\.[0-9]+)?)"


def _parse_area(seg: str) -> Optional[float]:
    """Hectares from an area/extent value in any of the common Indian notations:
    '2.5 ha', '2.50 Acres', 'Ac. 2.50 Cts' (acres.cents), '2 Acres 20 Guntas', '40 cents', '1200 sq.m'."""
    s = seg.lower()
    m = re.search(_NUM + r"\s*(?:hectares?|hect\.?|ha)\b", s)
    if m:
        return float(m.group(1))
    m = re.search(_NUM + r"\s*(?:acres?|ac)\b\.?\s*(?:" + _NUM + r"\s*(guntas?|gunthas?|g\b|cents?|cts?)\b)?", s)
    if m:
        ha = float(m.group(1)) * ACRE_TO_HA
        if m.group(2):
            ha += float(m.group(2)) * (GUNTA_TO_HA if m.group(3).startswith("g") else CENT_TO_HA)
        return ha
    m = re.search(r"\bac(?:res?)?\b\.?\s*" + _NUM, s)       # unit first: "Ac. 2.50 Cts" / "Acres 3"
    if m:
        return float(m.group(1)) * ACRE_TO_HA
    m = re.search(_NUM + r"\s*(?:guntas?|gunthas?)\b", s)
    if m:
        return float(m.group(1)) * GUNTA_TO_HA
    m = re.search(_NUM + r"\s*(?:cents?|cts)\b", s)
    if m:
        return float(m.group(1)) * CENT_TO_HA
    m = re.search(_NUM + r"\s*(?:sq\.?\s*m(?:etres?|eters?|trs?|t)?|m2|square\s*met(?:re|er)s?)\b", s)
    if m:
        return float(m.group(1)) * SQM_TO_HA
    return None


def _extract_area(text: str) -> Optional[Field]:
    for m in re.finditer(r"(?:total\s*)?(?:area|extent)(?:\s*(?:acquired|of\s*land|in\s*question))?" + SEP + r"([^\n]{1,60})", text, FLAGS):
        ha = _parse_area(m.group(1))
        if ha is not None and ha > 0:
            return Field(str(round(ha, 4)), _clean(m.group(0)), "high")
    return None


LAND_TYPE_WORDS = [
    ("agricultural", r"agricultur\w*|wet\s*land|dry\s*land|irrigated|cultivable|garden\s*land|orchard|nanja|punja|farm\s*land"),
    ("residential", r"residential|house\s*site|abadi|gramakantam|homestead|dwelling"),
    ("commercial", r"commercial"),
    ("barren", r"barren|waste\s*land|wasteland|banjar|uncultivable|unculturable"),
]
LAND_TYPE_LABEL = r"(?:land\s*type|type\s*of\s*land|nature\s*of\s*land|kind\s*of\s*land|land\s*use|classification|land\s*class(?:ification)?)"


def _match_land_type(s: str) -> Optional[str]:
    for value, pat in LAND_TYPE_WORDS:
        if re.search(rf"\b(?:{pat})\b", s, re.I):
            return value
    return None


def _extract_land_type(text: str) -> Optional[Field]:
    m = re.search(LAND_TYPE_LABEL + SEP + r"([^\n]{1,50})", text, FLAGS)
    if m:
        seg = m.group(1)
        v = _match_land_type(seg) or (_match_land_type("wet land") if re.search(r"\b(?:wet|dry)\b", seg, re.I) else None)
        if v:
            return Field(v, _clean(m.group(0)), "high")
    for value, pat in LAND_TYPE_WORDS:  # unlabelled keyword anywhere: plausible, but needs review
        m = re.search(rf"\b(?:{pat})\b", text, re.I)
        if m:
            return Field(value, m.group(0), "medium")
    return None


def _extract_amount(text: str) -> Optional[Field]:
    m = re.search(
        r"(?:(?:total\s*)?(?:amount\s*of\s*)?compensation(?:\s*(?:amount|payable|awarded|paid))*|"
        r"award(?:ed)?\s*amount|amount\s*(?:payable|awarded)|net\s*amount(?:\s*payable)?)"
        r"[^0-9\n₹]{0,25}?(?:rs\s*[.,:]?|inr|₹|rupees)?\s*"
        # Digit groups may carry OCR-inserted spaces around the commas: "84, 28,000".
        r"([0-9]+(?:[ ]?,[ ]?[0-9]{2,3})*(?:\.[0-9]{1,2})?)\s*(?:/-)?\s*(lakhs?|lacs?|crores?|cr\b)?",
        text, re.I,
    )
    if not m:
        return None
    try:
        raw_num = float(re.sub(r"[ ,]", "", m.group(1)))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    if unit.startswith(("lakh", "lac")):
        rupees = raw_num * 100_000
    elif unit.startswith("cr"):
        rupees = raw_num * 10_000_000
    else:
        rupees = raw_num
    # A land compensation under Rs 1,000 is almost certainly a partial read - flag it.
    return Field(f"{rupees:.0f}", _clean(m.group(0)), "high" if rupees >= 1000 else "medium")


def _extract_ref_no(text: str) -> Optional[Field]:
    # `no\b` so a heading like "AWARD NOTICE" is not read as "Award No."; the first labelled
    # value that actually contains a digit wins.
    for m in re.finditer(
        r"\b(?:notification|reference|ref|award|proceedings|procs|file|memo|order|rc|letter|case)\s*\.?\s*"
        r"(?:no|number)\b\s*\.?" + SEP + r"([A-Za-z0-9][A-Za-z0-9/\-.()]{2,40})",
        text, FLAGS,
    ):
        if re.search(r"\d", m.group(1)):
            return Field(_clean(m.group(1)), m.group(0), "high")
    return None


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _extract_date(text: str) -> Optional[Field]:
    label = r"(?:\bdate(?:d)?|\bdt|issued\s*on|date\s*of\s*(?:award|notification|issue))\s*\.?" + SEP
    m = re.search(label + r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})\b", text, FLAGS)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(label + r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s*)?([A-Za-z]{3,9})\.?,?\s*(\d{4})\b", text, FLAGS)
        if not m or m.group(2)[:3].lower() not in MONTHS:
            return None
        d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
    if y < 100:
        y += 2000
    value = f"{d:02d}/{mo:02d}/{y:04d}"
    try:
        date(y, mo, d)
        confidence = "high"
    except ValueError:
        confidence = "medium"  # e.g. an OCR misread like 42/03/2025 - keep it visible, flag it
    return Field(value, m.group(0), confidence)


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


def extract_fields(text: str, words: Optional[list[tuple[str, int]]] = None) -> dict:
    """Fields found in `text`. When OCR word confidences are given, a field containing any
    uncertain word is downgraded to "medium" so the reviewing officer checks it."""
    word_conf: dict[str, int] = {}
    for w, c in words or []:
        key = w.lower()
        word_conf[key] = min(c, word_conf.get(key, 100))
    out = {}
    for key, fn in EXTRACTORS.items():
        f = fn(text)
        if not f:
            continue
        confidence = f.confidence
        if word_conf and confidence == "high":
            tokens = [t.lower() for t in re.findall(r"[^\s:]+", f.raw)]
            confs = [word_conf[t] for t in tokens if t in word_conf]
            if confs and min(confs) < LOW_WORD_CONF:
                confidence = "medium"
        out[key] = {"value": f.value, "matched_text": f.raw, "confidence": confidence}
    return out


def _warnings(fields: dict, avg_conf: Optional[float], words: list, angle: float) -> list[str]:
    notes = []
    if avg_conf is not None and avg_conf < 70:
        notes.append(f"Low OCR confidence ({avg_conf:.0f}%) - the scan may be blurred, dark or low resolution; check every field.")
    if words:
        low = sum(1 for _, c in words if c < LOW_WORD_CONF)
        if low / len(words) > 0.25:
            notes.append(f"{low} of {len(words)} words were read with low confidence.")
    if "survey_no" not in fields:
        notes.append("No labelled survey number found - enter it manually to match a parcel.")
    if abs(angle) >= MIN_SKEW_DEG:
        notes.append(f"Page was tilted {abs(angle):.1f}° and has been straightened.")
    return notes


def run_ocr(data: bytes, filename: str) -> dict:
    """Top-level entry point. Never raises for expected 'not applicable' or OCR engine
    failures - always returns a status dict so a bad scan never blocks an upload."""
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTS:
        text = data.decode("utf-8", errors="ignore")
        fields = extract_fields(text)
        return {"status": "done", "engine": "plain-text", "pages": 1, "text": text, "ocr_confidence": 100.0,
                "fields": fields, "warnings": []}
    if ext not in IMAGE_EXTS and ext not in PDF_EXTS:
        return {"status": "skipped", "reason": f"'{ext}' is a text-based office format; OCR is not needed."}

    try:
        if ext in PDF_EXTS:
            layer = _pdf_text_layer(data)
            if layer:
                fields = extract_fields(layer)
                return {"status": "done", "engine": "pdf text layer", "pages": layer.count("\f") or 1,
                        "text": layer.replace("\f", "\n\n--- page break ---\n\n"), "ocr_confidence": 100.0,
                        "fields": fields, "warnings": _warnings(fields, None, [], 0.0)}

        import pytesseract

        images = _to_image_list(data, filename)
        if not images:
            return {"status": "skipped", "reason": "No pages found to process."}
        all_text, all_words, max_angle = [], [], 0.0
        for page in images:
            gray, angle = _preprocess(page)
            text, words = _ocr_best(gray)
            all_text.append(text)
            all_words.extend(words)
            max_angle = max(max_angle, abs(angle), key=abs)
        full_text = "\n\n--- page break ---\n\n".join(all_text)
        avg_conf = round(sum(c for _, c in all_words) / len(all_words), 1) if all_words else 0.0
        fields = extract_fields(full_text, all_words)
        version = str(pytesseract.get_tesseract_version())
        return {
            "status": "done", "engine": f"tesseract {version}", "pages": len(images),
            "text": full_text, "ocr_confidence": avg_conf, "fields": fields,
            "warnings": _warnings(fields, avg_conf, all_words, max_angle),
        }
    except Exception as e:  # defensive: a bad scan must never take the upload down
        return {"status": "failed", "error": str(e)[:300]}
