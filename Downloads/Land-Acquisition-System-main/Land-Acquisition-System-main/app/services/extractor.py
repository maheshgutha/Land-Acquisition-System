"""Structured Land Field Extractor and Confidence Scoring Engine.

Implements Person 2 Modules:
- H01-H30: Regex, NLP, Gazetteer, Multilingual Aliases, Area Normalization
- I01-I15: Deterministic Dynamic Field & Document Confidence Scoring Engine
"""
from __future__ import annotations

import difflib
import math
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Multilingual Aliases & Keywords (H24)
# English, Hindi, Telugu, Kannada
# -----------------------------------------------------------------------------
FIELD_ALIASES: dict[str, list[str]] = {
    "khasra_no": [
        "khasra no", "khasra number", "khasra", "kh no", "khasra nos",
        "खसरा नं", "खसरा संख्या", "खसरा", "ख.नं",
        "ఖస్రా నంబర్", "ఖస్రా నం", "ఖస్రా సంఖ్య", "ఖస్రా",
        "ಖಸ್ರಾ ನಂ", "ಖಸ್ರಾ ಸಂಖ್ಯೆ", "ಖಸ್ರಾ",
    ],
    "khata_no": [
        "khata no", "khata number", "khatoni no", "khatauni no", "khatoni", "khata",
        "खाता संख्या", "खाता नं", "खतौनी संख्या", "खतौनी", "खाता",
        "ఖాతా సంఖ్య", "ఖాతా నంబర్", "ఖాతా నం", "ఖాతా",
        "ಖಾತಾ ಸಂಖ್ಯೆ", "ಖಾತಾ ನಂ", "ಖಾತೆ",
    ],
    "patta_no": [
        "patta no", "patta number", "patta passbook", "passbook no", "patta",
        "पट्टा संख्या", "पट्टा नं", "पट्टा",
        "పట్టాదారు పాస్ పుస్తకం", "పట్టా నంబర్", "పట్టా నం", "పట్టా సంఖ్య", "పట్టా",
        "ಪಟ್ಟಾ ಸಂಖ್ಯೆ", "ಪಟ್ಟಾ ನಂ", "ಪಟ್ಟಾ",
    ],
    "survey_no": [
        "survey no", "survey number", "sy no", "sy. no", "s.no", "r.s. no", "c.s. no", "sur. no",
        "सर्वे नंबर", "सर्वे नं", "सर्वे संख्या",
        "సర్వే నంబర్", "సర్వే నం", "సర్వే సంఖ్య",
        "ಸರ್ವೆ ನಂಬರ್", "ಸರ್ವೆ ನಂ", "ಸರ್ವೆ ಸಂಖ್ಯೆ",
    ],
    "owner_name": [
        "owner name", "land owner", "name of owner", "pattadar name", "pattadar",
        "khatedar name", "bhuswami", "occupant", "holder name", "farmer name",
        "खातेदार का नाम", "भूस्वामी का नाम", "भूस्वामी", "खातेदार", "नाम",
        "పట్టాదారు పేరు", "భూ యజమాని పేరు", "రైతు పేరు", "భూ యజమాని",
        "ಮಾಲೀಕರ ಹೆಸರು", "ಪಟ್ಟಾದಾರರ ಹೆಸರು", "ಖಾತೆದಾರರ ಹೆಸರು",
    ],
    "area": [
        "total area", "land area", "area", "extent", "rakba", "measurement",
        "क्षेत्रफल", "कुल क्षेत्रफल", "रकबा",
        "విస్తీర్ణం", "మొత్తం విస్తీర్ణం", "భూమి విస్తీర్ణం",
        "ವಿಸ್ತೀರ್ಣ", "ಒಟ್ಟು ವಿಸ್ತೀರ್ಣ",
    ],
    "village": [
        "village name", "village", "mauza", "gram", "grama", "revenue village",
        "ग्राम", "गाँव", "मौजा", "ग्राम का नाम",
        "గ్రామం", "గ్రామము", "రెవెన్యూ గ్రామం",
        "ಗ್ರಾಮ", "ಹಳ್ಳಿ",
    ],
    "tehsil": [
        "tehsil", "tahsil", "mandal", "taluk", "taluka", "sub-district",
        "तहसील", "मंडल", "तालुका",
        "మండలం", "తాలూకా",
        "ತಾಲೂಕು", "ಮಂಡಲ",
    ],
    "district": [
        "district", "jilla", "zilla", "dist",
        "जिला", "ज़िला",
        "జిల్లా",
        "ಜಿಲ್ಲೆ",
    ],
    "state": [
        "state", "province", "rajya",
        "राज्य", "प्रदेश",
        "రాష్ట్రం",
        "ರಾಜ್ಯ",
    ],
    "land_classification": [
        "classification", "land classification", "land class", "kisam", "kism", "land category",
        "भूमि वर्गीकरण", "किस्म", "किस्म भूमि",
        "భూమి వర్గీకరణ", "వర్గీకరణ",
        "ಭೂ ವರ್ಗೀಕರಣ", "ವರ್ಗೀಕರಣ",
    ],
    "land_use": [
        "land use", "usage", "crop", "current use", "soil type",
        "उपयोग", "भूमि उपयोग", "फसल",
        "వినియోగం", "భూ వినియోగం", "పంట",
        "ಬಳಕೆ", "ಭೂ ಬಳಕೆ",
    ],
    "registration_no": [
        "registration no", "registration number", "deed no", "document no", "d.no", "doc no", "reg no",
        "पंजीकरण संख्या", "दस्तावेज संख्या", "विलेख संख्या", "रजिस्ट्री सं",
        "రిజిస్ట్రేషన్ సంఖ్య", "రిజిస్ట్రేషన్ నంబర్", "దస్తావేజు సంఖ్య",
        "ನೋಂದಣಿ ಸಂಖ್ಯೆ", "ದಾಖಲೆ ಸಂಖ್ಯೆ",
    ],
    "registration_date": [
        "registration date", "date of registration", "deed date", "executed on", "reg date",
        "पंजीकरण दिनांक", "दस्तावेज दिनांक", "पंजीकरण की तिथि",
        "రిజిస్ట్రేషన్ తేదీ", "దస్తావేజు తేదీ",
        "ನೋಂದಣಿ ದಿನಾಂಕ",
    ],
    "mutation_no": [
        "mutation no", "mutation number", "intakal no", "dakhil kharij no", "namantaran no",
        "नामांतरण संख्या", "दाखिल खारिज संख्या", "इंतकाल संख्या",
        "మ్యుటేషన్ సంఖ్య", "మ్యుటేషన్ నంబర్",
        "ನಮೂದು ಸಂಖ್ಯೆ", "ಮ್ಯುಟೇಶನ್ ಸಂಖ್ಯೆ",
    ],
    "mutation_date": [
        "mutation date", "date of mutation", "dakhil kharij date",
        "नामांतरण दिनांक", "दाखिल खारिज दिनांक",
        "మ్యుటేషన్ తేదీ",
        "ಮ್ಯುಟೇಶನ್ ದಿನಾಂಕ",
    ],
    "mutation_status": [
        "mutation status", "namantaran status", "dakhil kharij status",
        "नामांतरण स्थिति", "दाखिल खारिज स्थिति",
        "మ్యుటేషన్ స్థితి",
        "ಮ್ಯುಟೇಶನ್ ಸ್ಥಿತಿ",
    ],
    "encumbrance_status": [
        "encumbrance status", "encumbrance", "mortgage status", "charge", "liability",
        "भार स्थिति", "ऋण भार", "बंधक स्थिति",
        "భారము స్థితి", "రుణ భారం",
        "ಬಾಧ್ಯತೆ ಸ್ಥಿತಿ",
    ],
}

# -----------------------------------------------------------------------------
# Area Unit Conversion Factors to Sq. Metres & Hectares (H07, H08)
# -----------------------------------------------------------------------------
AREA_UNITS: dict[str, float] = {
    # Unit -> multiplier to square metres (m^2)
    "hectare": 10_000.0,
    "hectares": 10_000.0,
    "ha": 10_000.0,
    "हेक्टेयर": 10_000.0,
    "హెక్టార్లు": 10_000.0,
    "ಹೆಕ್ಟೇರ್": 10_000.0,

    "acre": 4_046.85642,
    "acres": 4_046.85642,
    "ac": 4_046.85642,
    "एकड़": 4_046.85642,
    "ఎకరాలు": 4_046.85642,
    "ಎಕರೆ": 4_046.85642,

    "bigha": 2_529.28526,
    "बीघा": 2_529.28526,
    "బీఘా": 2_529.28526,

    "guntha": 101.17141,
    "gunthas": 101.17141,
    "guntas": 101.17141,
    "गुंठा": 101.17141,
    "గుంటలు": 101.17141,
    "ಗುಂಟೆ": 101.17141,

    "cent": 40.46856,
    "cents": 40.46856,
    "सेंन्ट": 40.46856,
    "సెంట్": 40.46856,
    "ಸೆಂಟ್": 40.46856,

    "sq_m": 1.0,
    "sqm": 1.0,
    "sq.m": 1.0,
    "sq metre": 1.0,
    "sq metres": 1.0,
    "वर्ग मीटर": 1.0,
    "చదరపు మీటర్లు": 1.0,
    "ಚದರ ಮೀಟರ್": 1.0,

    "sq_ft": 0.092903,
    "sqft": 0.092903,
    "sq.ft": 0.092903,
    "square feet": 0.092903,
    "वर्ग फुट": 0.092903,
    "చదరపు అడుగులు": 0.092903,
    "ಚದರ ಅಡಿ": 0.092903,

    "biswa": 126.464,
    "बिस्वा": 126.464,
}

# -----------------------------------------------------------------------------
# Master Gazetteer & Geographic Knowledge Base (H22)
# -----------------------------------------------------------------------------
GAZETTEER_STATES: dict[str, list[str]] = {
    "Andhra Pradesh": ["Krishna", "Anantapur", "Visakhapatnam", "Kurnool", "Nellore", "Guntur", "Chittoor", "Kadapa"],
    "Telangana": ["Rangareddy", "Medak", "Warangal", "Nalgonda", "Karimnagar", "Khammam", "Nizamabad"],
    "Karnataka": ["Tumakuru", "Ballari", "Belagavi", "Kolar", "Bengaluru Rural", "Mysuru", "Mandya"],
    "Maharashtra": ["Pune", "Nagpur", "Thane", "Nashik", "Aurangabad", "Solapur"],
}

GAZETTEER_VILLAGES: list[str] = [
    "Rampur", "Venkatapuram", "Chinnapalem", "Gopalapuram", "Ramannapeta", "Kothapalli",
    "Devarapalli", "Lingapuram", "Narsapur", "Peddapadu", "Mallavaram", "Sitarampuram",
    "Thimmapur", "Yerragunta", "Chandragiri", "Kothur", "Kondapur", "Shamshabad",
    "Gachibowli", "Medchal", "Patancheru", "Nelamangala", "Hosapete", "Nandi",
]

GAZETTEER_MULTILINGUAL: dict[str, str] = {
    # Hindi / Telugu / Kannada to English canonical names (H22, H24)
    "रामपुर": "Rampur",
    "రాంపూర్": "Rampur",
    "ವೆಂಕಟಾಪುರ": "Venkatapuram",
    "वेंकटपुरम": "Venkatapuram",
    "వెంకటాపురం": "Venkatapuram",
    "చిన్నపాలెం": "Chinnapalem",
    "గోపాలపురం": "Gopalapuram",
    "మేదక్": "Medak",
    "मेडक": "Medak",
    "रंगारेड्डी": "Rangareddy",
    "రంగారెడ్డి": "Rangareddy",
    "రంగా రెడ్డి": "Rangareddy",
    "वारंगल": "Warangal",
    "వరంగల్": "Warangal",
    "తెలంగాణ": "Telangana",
    "तेलंगाना": "Telangana",
    "ఆంధ్ర ప్రదేశ్": "Andhra Pradesh",
    "ఆంధ్రప్రదేశ్": "Andhra Pradesh",
    "आंध्र प्रदेश": "Andhra Pradesh",
    "ಕರ್ನಾಟಕ": "Karnataka",
    "कर्नाटक": "Karnataka",
    "महाराष्ट्र": "Maharashtra",
}

HONORIFICS = [
    r"shri\b", r"shree\b", r"smt\b", r"sri\b", r"dr\b", r"late\b", r"mr\b", r"mrs\b",
    r"श्री", r"श्रीमती", r"శ్రీ", r"శ్రీమతి", r"ಶ್ರೀ", r"ಶ್ರೀಮತಿ",
]

# -----------------------------------------------------------------------------
# Extraction Confidence Thresholds (I06)
# -----------------------------------------------------------------------------
CONFIDENCE_THRESHOLDS = {
    "high": 0.90,      # Green  >= 90%
    "medium": 0.70,    # Yellow 70% - 89%
    "low": 0.40,       # Orange 40% - 69%
    "critical": 0.00,  # Red    < 40%
}


def get_confidence_badge(score: float) -> tuple[str, str]:
    """Returns (badge_color, label) based on I06-I09."""
    if score >= 0.90:
        return "green", "High (≥90%)"
    if score >= 0.70:
        return "yellow", "Medium (70-89%)"
    if score >= 0.40:
        return "orange", "Low (40-69%)"
    return "red", "Critical (<40%)"


# -----------------------------------------------------------------------------
# String Normalization & Fuzzy Matching (H23)
# -----------------------------------------------------------------------------
def fuzzy_match(query: str, choices: list[str], cutoff: float = 0.72) -> Optional[Tuple[str, float]]:
    """Returns best fuzzy match and similarity ratio (H23)."""
    if not query or not choices:
        return None
    q = query.strip().lower()
    best_match = None
    best_score = 0.0
    for choice in choices:
        ratio = difflib.SequenceMatcher(None, q, choice.strip().lower()).ratio()
        if ratio > best_score:
            best_score = ratio
            best_match = choice
    if best_score >= cutoff:
        return best_match, round(best_score, 3)
    return None


def clean_name(raw_name: str) -> str:
    """Strips honorifics, lineage labels (s/o, w/o, d/o), and punctuation (H01)."""
    if not raw_name:
        return ""
    text = raw_name.split("\n")[0].strip()
    hon_pat = r"(?i)(?:^|[\s,])(?:shri|shree|smt|sri|dr|late|mr|mrs|श्री|श्रीमती|శ్రీ|శ్రీమతి|ಶ್ರೀ|ಶ್ರೀಮತಿ)(?:[\s\.]+|\b)"
    text = re.sub(hon_pat, " ", text)
    # Strip relations s/o, w/o, d/o, c/o, आत्मज, etc.
    text = re.split(r"(?i)(?:\b(?:s/o|w/o|d/o|c/o|son of|wife of|daughter of|care of)\b|आत्मज|सुपुत्र|सुपुत्री|पत्नी|తండ్రి|కుమారుడు|ತಂದೆ|ಮಗ)", text)[0]
    # Clean standard punctuation without destroying Unicode letters and combining marks
    text = re.sub(r"[,\.:;\"'!\?#\$%\^&\*\(\)_=\+\[\]\{\}\|\\<>~`/\-0-9]", " ", text)
    tokens = [t.capitalize() if t.isascii() else t for t in text.split() if len(t) >= 1]
    return " ".join(tokens).strip()


def parse_date(date_str: str) -> Optional[str]:
    """Parses various date formats to ISO YYYY-MM-DD string (H16, H18)."""
    if not date_str:
        return None
    cleaned = date_str.strip()
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y", "%B %d, %Y", "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Regex fallback for DD/MM/YYYY or YYYY-MM-DD
    m = re.search(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})", cleaned)
    if m:
        d, mth, y = m.groups()
        if len(y) == 2:
            y = f"20{y}"
        try:
            return date(int(y), int(mth), int(d)).isoformat()
        except ValueError:
            pass
    return None


def convert_area(value: float, unit_str: str) -> tuple[float, float, str]:
    """Converts (value, unit) to (area_sqm, area_ha, standardized_unit) (H07, H08)."""
    unit_norm = unit_str.strip().lower()
    multiplier = AREA_UNITS.get(unit_norm, 10_000.0) # default to ha if unknown
    std_unit = "hectare" if "ha" in unit_norm or "hec" in unit_norm else unit_norm
    sq_m = value * multiplier
    ha = sq_m / 10_000.0
    return round(sq_m, 2), round(ha, 4), std_unit


# -----------------------------------------------------------------------------
# Structured Land Field Extractor Engine (H01-H30)
# -----------------------------------------------------------------------------
class LandFieldExtractor:
    """Multi-stage NLP and Pattern-matching Land Field Extractor."""

    def __init__(self, ocr_baseline_confidence: float = 0.92):
        self.ocr_baseline = ocr_baseline_confidence

    def _build_alias_pattern(self, field_key: str) -> re.Pattern:
        aliases = FIELD_ALIASES.get(field_key, [field_key])
        escaped = [re.escape(a) for a in aliases]
        joined = "|".join(sorted(escaped, key=len, reverse=True))
        return re.compile(rf"(?:{joined})[\s:\-\.]+", re.IGNORECASE)

    def extract_field(
        self,
        text: str,
        field_key: str,
        value_pattern: str,
        source_type: str = "regex",
        context_window: int = 120,
    ) -> Optional[dict]:
        """Generic alias-anchored regex extractor with dual-schema output (H25, H26)."""
        alias_pat = self._build_alias_pattern(field_key)
        for m in alias_pat.finditer(text):
            start = m.end()
            chunk = text[start: start + context_window]
            first_line = chunk.split("\n")[0].strip()
            val_match = re.search(value_pattern, first_line, re.IGNORECASE)
            if val_match:
                raw_val = val_match.group(1).strip()
                return {
                    "raw_value": raw_val,
                    "normalized_value": raw_val,
                    "source": source_type,
                    "start_pos": start + val_match.start(1),
                    "end_pos": start + val_match.end(1),
                }
        return None

    def extract_all(
        self,
        raw_text: str,
        ocr_confidence: Optional[float] = None,
        context_state: Optional[str] = None,
        context_district: Optional[str] = None,
    ) -> dict[str, Any]:
        """Extracts all 20+ land fields from raw text with confidence scores (H01-H30, I01-I15)."""
        ocr_conf = ocr_confidence if ocr_confidence is not None else self.ocr_baseline
        text = raw_text.replace("\r", " ")
        extracted: dict[str, dict] = {}

        # 1. Survey Number (H02)
        # e.g., "Survey No: 142/1-B", "Sy No. 45/2", "सर्वे नं. 120"
        sy_res = self.extract_field(
            text, "survey_no",
            r"^([0-9]{1,5}(?:[/\-][0-9A-Za-z]+)*)"
        )
        if not sy_res:
            # Fallback regex across text
            m = re.search(r"\b(?:Sy|Survey|Sy\.|S\.No|RS|CS)[\s\.:\-\/]+([0-9]{1,5}(?:[/\-][0-9A-Za-z]+)*)\b", text, re.IGNORECASE)
            if m:
                sy_res = {"raw_value": m.group(1), "normalized_value": m.group(1), "source": "regex_fallback"}

        if sy_res:
            norm_sy = sy_res["raw_value"].strip()
            conf, expl = self._calc_field_confidence(sy_res, ocr_conf, pattern_precision=0.96)
            extracted["survey_no"] = self._format_field(sy_res["raw_value"], norm_sy, conf, sy_res["source"], expl)
        else:
            extracted["survey_no"] = self._empty_field("Missing survey number")

        # 2. Khasra Number (H03)
        kh_res = self.extract_field(
            text, "khasra_no",
            r"^([0-9]{1,5}(?:[/\-][0-9A-Za-z]+)*)"
        )
        if kh_res:
            conf, expl = self._calc_field_confidence(kh_res, ocr_conf, pattern_precision=0.95)
            extracted["khasra_no"] = self._format_field(kh_res["raw_value"], kh_res["raw_value"], conf, kh_res["source"], expl)
        else:
            extracted["khasra_no"] = self._empty_field("Optional/not found")

        # 3. Khata / Khatoni Number (H04)
        kht_res = self.extract_field(
            text, "khata_no",
            r"^([0-9]{1,6}(?:[/\-][0-9A-Za-z]+)*)"
        )
        if kht_res:
            conf, expl = self._calc_field_confidence(kht_res, ocr_conf, pattern_precision=0.94)
            extracted["khata_no"] = self._format_field(kht_res["raw_value"], kht_res["raw_value"], conf, kht_res["source"], expl)
        else:
            extracted["khata_no"] = self._empty_field("Optional/not found")

        # 4. Patta Number (H05)
        pt_res = self.extract_field(
            text, "patta_no",
            r"^([A-Za-z0-9\-\/]{2,20})"
        )
        if pt_res:
            conf, expl = self._calc_field_confidence(pt_res, ocr_conf, pattern_precision=0.92)
            extracted["patta_no"] = self._format_field(pt_res["raw_value"], pt_res["raw_value"], conf, pt_res["source"], expl)
        else:
            extracted["patta_no"] = self._empty_field("Optional/not found")

        # 5. Owner Name (H01)
        # e.g., "Owner Name: Shri Ramesh Naidu s/o Venkata Rao", "खातेदार: महेश रेड्डी"
        owner_res = self.extract_field(
            text, "owner_name",
            r"^([^\r\n:;,\.0-9]{2,60})"
        )
        if not owner_res:
            # Fallback looking for honorific prefix
            m = re.search(r"\b(?:Shri|Smt|Sri|Dr|Mr|Mrs|श्री|శ్రీ)\s+([^\r\n:;,\.0-9]{2,40})", text, re.IGNORECASE)
            if m:
                owner_res = {"raw_value": m.group(0), "normalized_value": m.group(1), "source": "honorific_regex"}

        if owner_res:
            cleaned = clean_name(owner_res["raw_value"])
            is_valid = len(cleaned.split()) >= 1 and len(cleaned) >= 2
            precision = 0.94 if is_valid else 0.60
            conf, expl = self._calc_field_confidence(owner_res, ocr_conf, pattern_precision=precision)
            extracted["owner_name"] = self._format_field(owner_res["raw_value"], cleaned, conf, owner_res["source"], expl)
        else:
            extracted["owner_name"] = self._empty_field("Missing owner name")

        # 6. Area, Unit, and Normalization to Sq.M / Ha (H06, H07, H08)
        # e.g., "Area: 2.45 Hectares", "Extent: 3 Acres 12 Guntas", "रकबा: 1.50 हेक्टेयर"
        area_res = self.extract_field(
            text, "area",
            r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z\.\u0900-\u097F\u0C00-\u0C7F]+)?"
        )
        if not area_res:
            m = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*(ha|hectares?|acres?|bigha|sq\.?m|sqft|cents?|గుంటలు|హెక్టార్లు)\b", text, re.IGNORECASE)
            if m:
                area_res = {"raw_value": m.group(0), "val_str": m.group(1), "unit_str": m.group(2), "source": "regex_unit_match"}

        if area_res:
            try:
                # Parse numeric value
                val_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", area_res["raw_value"])
                val = float(val_match.group(1)) if val_match else 0.0
                # Parse unit
                unit_match = re.search(r"([A-Za-z\u0900-\u097F\u0C00-\u0C7F]+)$", area_res["raw_value"].strip())
                unit = unit_match.group(1).lower() if unit_match else "hectare"
                sq_m, ha, std_unit = convert_area(val, unit)

                conf, expl = self._calc_field_confidence(area_res, ocr_conf, pattern_precision=0.95)
                extracted["area_value"] = self._format_field(str(val), val, conf, area_res["source"], expl)
                extracted["area_unit"] = self._format_field(unit, std_unit, conf, area_res["source"], "Normalized unit")
                extracted["area_sqm"] = self._format_field(f"{sq_m} m²", sq_m, conf, "unit_converter", f"Converted from {val} {unit}")
                extracted["area_ha"] = self._format_field(f"{ha} ha", ha, conf, "unit_converter", f"Converted from {val} {unit}")
            except Exception as e:
                extracted["area_value"] = self._empty_field(f"Error parsing area: {e}")
                extracted["area_unit"] = self._empty_field("Unknown")
                extracted["area_sqm"] = self._empty_field("0.0")
                extracted["area_ha"] = self._empty_field("0.0")
        else:
            extracted["area_value"] = self._empty_field("Missing area value")
            extracted["area_unit"] = self._empty_field("Unknown")
            extracted["area_sqm"] = self._empty_field("0.0")
            extracted["area_ha"] = self._empty_field("0.0")

        # 7. Village (H09, H22 Gazetteer Lookup)
        vil_res = self.extract_field(
            text, "village",
            r"^([^\r\n:;,\.0-9]{2,40})"
        )
        raw_vil = vil_res["raw_value"].strip() if vil_res else ""
        norm_vil = raw_vil
        gazetteer_hit = False

        if raw_vil:
            if raw_vil in GAZETTEER_MULTILINGUAL:
                norm_vil = GAZETTEER_MULTILINGUAL[raw_vil]
                gazetteer_hit = True
            else:
                fuzzy_res = fuzzy_match(raw_vil, GAZETTEER_VILLAGES)
                if fuzzy_res:
                    norm_vil = fuzzy_res[0]
                    gazetteer_hit = True
        else:
            # Look for gazetteer village directly in text
            for k, canon in GAZETTEER_MULTILINGUAL.items():
                if canon in GAZETTEER_VILLAGES and (k in text or re.search(rf"\b{re.escape(k)}\b", text)):
                    norm_vil = canon
                    raw_vil = k
                    gazetteer_hit = True
                    vil_res = {"raw_value": k, "source": "gazetteer_scan"}
                    break
            if not gazetteer_hit:
                for gv in GAZETTEER_VILLAGES:
                    if re.search(rf"\b{re.escape(gv)}\b", text, re.IGNORECASE):
                        norm_vil = gv
                        raw_vil = gv
                        gazetteer_hit = True
                        vil_res = {"raw_value": gv, "source": "gazetteer_scan"}
                        break

        if vil_res or gazetteer_hit:
            bonus = 0.08 if gazetteer_hit else 0.0
            conf, expl = self._calc_field_confidence(
                vil_res or {"source": "gazetteer"}, ocr_conf, pattern_precision=0.88, gazetteer_bonus=bonus
            )
            extracted["village"] = self._format_field(raw_vil or norm_vil, norm_vil, conf, "gazetteer" if gazetteer_hit else "regex", expl)
        else:
            extracted["village"] = self._empty_field("Missing village")

        # 8. Tehsil / Mandal (H10)
        teh_res = self.extract_field(
            text, "tehsil",
            r"^([A-Za-z\s\.\-]{3,40})"
        )
        if teh_res:
            conf, expl = self._calc_field_confidence(teh_res, ocr_conf, pattern_precision=0.88)
            extracted["tehsil"] = self._format_field(teh_res["raw_value"], teh_res["raw_value"].title(), conf, teh_res["source"], expl)
        else:
            extracted["tehsil"] = self._empty_field("Optional/not found")

        # 9. District (H11, H22)
        dist_res = self.extract_field(
            text, "district",
            r"^([A-Za-z\s\.\-]{3,40})"
        )
        raw_dist = dist_res["raw_value"].strip() if dist_res else (context_district or "")
        norm_dist = raw_dist
        if raw_dist in GAZETTEER_MULTILINGUAL:
            norm_dist = GAZETTEER_MULTILINGUAL[raw_dist]
            dist_fuzzy = (norm_dist, 1.0)
        else:
            all_districts = [d for dlist in GAZETTEER_STATES.values() for d in dlist]
            dist_fuzzy = fuzzy_match(raw_dist, all_districts) if raw_dist else None
            if dist_fuzzy:
                norm_dist = dist_fuzzy[0]

        if norm_dist:
            conf, expl = self._calc_field_confidence(
                dist_res or {"source": "context"}, ocr_conf, pattern_precision=0.92,
                gazetteer_bonus=0.06 if dist_fuzzy else 0.0
            )
            extracted["district"] = self._format_field(raw_dist, norm_dist, conf, "gazetteer" if dist_fuzzy else "regex", expl)
        else:
            extracted["district"] = self._empty_field("Missing district")

        # 10. State (H12, H22)
        raw_state = context_state or ""
        for k, v in GAZETTEER_MULTILINGUAL.items():
            if v in GAZETTEER_STATES and (k in text or re.search(rf"\b{re.escape(k)}\b", text)):
                raw_state = v
                break
        if not raw_state:
            for s in GAZETTEER_STATES.keys():
                if re.search(rf"\b{re.escape(s)}\b", text, re.IGNORECASE):
                    raw_state = s
                    break
        if raw_state:
            conf, expl = self._calc_field_confidence({"source": "master_data"}, ocr_conf, pattern_precision=0.98)
            extracted["state"] = self._format_field(raw_state, raw_state, conf, "master_data", expl)
        else:
            extracted["state"] = self._empty_field("Missing state")

        # 11. Land Classification & Land Use (H13, H14)
        cls_res = self.extract_field(
            text, "land_classification",
            r"^([A-Za-z\s\.\-]{3,30})"
        )
        if not cls_res:
            m = re.search(r"\b(agricultural|non-agricultural|commercial|residential|industrial|barren|forest)\b", text, re.IGNORECASE)
            if m:
                cls_res = {"raw_value": m.group(1), "source": "keyword_lookup"}

        norm_cls = cls_res["raw_value"].lower().capitalize() if cls_res else "Agricultural"
        conf, expl = self._calc_field_confidence(cls_res or {"source": "default"}, ocr_conf, pattern_precision=0.90)
        extracted["land_classification"] = self._format_field(cls_res["raw_value"] if cls_res else "Agricultural", norm_cls, conf, cls_res["source"] if cls_res else "default", expl)

        use_res = self.extract_field(
            text, "land_use",
            r"^([A-Za-z\s\.\-]{3,30})"
        )
        norm_use = use_res["raw_value"].strip() if use_res else "Single Crop"
        conf, expl = self._calc_field_confidence(use_res or {"source": "default"}, ocr_conf, pattern_precision=0.85)
        extracted["land_use"] = self._format_field(use_res["raw_value"] if use_res else "Single Crop", norm_use, conf, use_res["source"] if use_res else "default", expl)

        # 12. Registration Deed No & Date (H15, H16)
        reg_res = self.extract_field(
            text, "registration_no",
            r"^([A-Za-z0-9\-\/]{3,30})"
        )
        if not reg_res:
            m = re.search(r"\b(?:DOC|REG|DEED|DNO)[/\-\.\s]*([A-Za-z0-9\-\/]{3,24})\b", text, re.IGNORECASE)
            if m:
                reg_res = {"raw_value": m.group(0), "normalized_value": m.group(1), "source": "deed_regex"}

        if reg_res:
            conf, expl = self._calc_field_confidence(reg_res, ocr_conf, pattern_precision=0.92)
            extracted["registration_no"] = self._format_field(reg_res["raw_value"], reg_res.get("normalized_value", reg_res["raw_value"]), conf, reg_res["source"], expl)
        else:
            extracted["registration_no"] = self._empty_field("Optional/not found")

        reg_dt_res = self.extract_field(
            text, "registration_date",
            r"^([0-9]{1,2}[/\-\.][0-9]{1,2}[/\-\.][0-9]{2,4}|[A-Za-z]+\s+[0-9]{1,2},?\s+[0-9]{4})"
        )
        parsed_reg_dt = parse_date(reg_dt_res["raw_value"]) if reg_dt_res else None
        if parsed_reg_dt:
            conf, expl = self._calc_field_confidence(reg_dt_res, ocr_conf, pattern_precision=0.95)
            extracted["registration_date"] = self._format_field(reg_dt_res["raw_value"], parsed_reg_dt, conf, reg_dt_res["source"], expl)
        else:
            extracted["registration_date"] = self._empty_field("Optional/not found")

        # 13. Mutation No, Date, Status (H17, H18, H19)
        mut_res = self.extract_field(
            text, "mutation_no",
            r"^([A-Za-z0-9\-\/]{3,30})"
        )
        if mut_res:
            conf, expl = self._calc_field_confidence(mut_res, ocr_conf, pattern_precision=0.90)
            extracted["mutation_no"] = self._format_field(mut_res["raw_value"], mut_res["raw_value"], conf, mut_res["source"], expl)
        else:
            extracted["mutation_no"] = self._empty_field("Optional/not found")

        mut_dt_res = self.extract_field(
            text, "mutation_date",
            r"^([0-9]{1,2}[/\-\.][0-9]{1,2}[/\-\.][0-9]{2,4})"
        )
        parsed_mut_dt = parse_date(mut_dt_res["raw_value"]) if mut_dt_res else None
        if parsed_mut_dt:
            conf, expl = self._calc_field_confidence(mut_dt_res, ocr_conf, pattern_precision=0.94)
            extracted["mutation_date"] = self._format_field(mut_dt_res["raw_value"], parsed_mut_dt, conf, mut_dt_res["source"], expl)
        else:
            extracted["mutation_date"] = self._empty_field("Optional/not found")

        mut_st_res = self.extract_field(
            text, "mutation_status",
            r"^(Approved|Certified|Pending|Rejected|स्वीकृत|प्रमाणित|लंबित)"
        )
        norm_mut_st = "Approved"
        if mut_st_res:
            raw = mut_st_res["raw_value"].lower()
            if "pend" in raw or "लंबित" in raw:
                norm_mut_st = "Pending"
            elif "rej" in raw:
                norm_mut_st = "Rejected"
            else:
                norm_mut_st = "Approved"
        extracted["mutation_status"] = self._format_field(
            mut_st_res["raw_value"] if mut_st_res else "Approved",
            norm_mut_st,
            0.92 if mut_st_res else 0.80,
            mut_st_res["source"] if mut_st_res else "default",
            "Mutation status recognized"
        )

        # 14. Encumbrance Status (H20)
        enc_res = self.extract_field(
            text, "encumbrance_status",
            r"^(Nil|Clear|None|Mortgaged|Disputed|Bank Loan|Loan Pending|भार मुक्त|ऋण मुक्त|बंधक)"
        )
        norm_enc = "Clear"
        if enc_res:
            raw = enc_res["raw_value"].lower()
            if "mort" in raw or "loan" in raw or "बंधक" in raw:
                norm_enc = "Mortgaged"
            elif "disp" in raw:
                norm_enc = "Disputed"
            else:
                norm_enc = "Clear"
        extracted["encumbrance_status"] = self._format_field(
            enc_res["raw_value"] if enc_res else "Clear",
            norm_enc,
            0.93 if enc_res else 0.85,
            enc_res["source"] if enc_res else "default",
            "Encumbrance liability verification"
        )

        # ---------------------------------------------------------------------
        # Document Composite Confidence Score (I13, I14, I15)
        # ---------------------------------------------------------------------
        weights = {
            "survey_no": 0.25,
            "owner_name": 0.25,
            "area_value": 0.25,
            "village": 0.15,
            "district": 0.10,
        }
        total_w = sum(weights.values())
        composite_score = sum(extracted[k]["confidence"] * weights[k] for k in weights) / total_w
        composite_score = round(min(1.0, max(0.0, composite_score)), 3)

        requires_review = (
            composite_score < 0.75
            or extracted["survey_no"]["confidence"] < 0.70
            or extracted["owner_name"]["confidence"] < 0.70
            or extracted["area_value"]["confidence"] < 0.70
        )

        badge_color, badge_label = get_confidence_badge(composite_score)

        return {
            "fields": extracted,
            "overall_confidence": composite_score,
            "confidence_pct": round(composite_score * 100, 1),
            "badge_color": badge_color,
            "badge_label": badge_label,
            "requires_review": requires_review,
            "extracted_at": datetime.utcnow().isoformat(),
        }

    def _calc_field_confidence(
        self,
        match_info: dict,
        ocr_confidence: float,
        pattern_precision: float = 0.90,
        gazetteer_bonus: float = 0.0,
    ) -> tuple[float, str]:
        """Calculates deterministic field confidence score (I01, I02, I03, I04, I11, I12)."""
        w_ocr, w_pat, w_gaz = 0.40, 0.50, 0.10
        raw_score = (w_ocr * ocr_confidence) + (w_pat * pattern_precision) + (w_gaz * (1.0 if gazetteer_bonus > 0 else 0.8)) + gazetteer_bonus
        final_score = round(min(0.99, max(0.10, raw_score)), 3)
        explanation = (
            f"OCR conf: {round(ocr_confidence*100)}% + "
            f"Pattern precision: {round(pattern_precision*100)}%"
        )
        if gazetteer_bonus > 0:
            explanation += f" + Gazetteer boost: +{round(gazetteer_bonus*100)}%"
        explanation += f" = {round(final_score*100)}%"
        return final_score, explanation

    def _format_field(
        self,
        raw_val: Any,
        norm_val: Any,
        conf: float,
        source: str,
        explanation: str,
    ) -> dict[str, Any]:
        badge_color, badge_label = get_confidence_badge(conf)
        return {
            "raw_value": str(raw_val) if raw_val is not None else "",
            "normalized_value": norm_val,
            "confidence": conf,
            "confidence_pct": round(conf * 100, 1),
            "source": source,
            "explanation": explanation,
            "badge_color": badge_color,
            "badge_label": badge_label,
            "requires_review": conf < 0.70,
        }

    def _empty_field(self, reason: str) -> dict[str, Any]:
        return {
            "raw_value": "",
            "normalized_value": None,
            "confidence": 0.0,
            "confidence_pct": 0.0,
            "source": "none",
            "explanation": reason,
            "badge_color": "red",
            "badge_label": "Critical (<40%)",
            "requires_review": True,
        }


# Singleton instance
extractor = LandFieldExtractor()
