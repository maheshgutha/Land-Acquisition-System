"""Tests for Person 2 deliverables:
- H01-H30: Structured Land Field Extractor
- I01-I15: Confidence Scoring Engine
- J01-J30: Land Record Business Rule Engine & GIS Area Mismatch Calculator
"""
import pytest
from app.services.extractor import extractor, convert_area, clean_name, fuzzy_match, get_confidence_badge
from app.services.land_validator import validator
from app.geo import rectangle


def test_clean_name_honorifics_and_relations():
    """H01: Primary owner name extracted cleanly, stripping honorifics and lineage."""
    assert clean_name("Shri Ramesh Naidu s/o Venkata Rao") == "Ramesh Naidu"
    assert clean_name("Smt. Lakshmi Devi w/o Suresh Reddy") == "Lakshmi Devi"
    assert clean_name("श्री महेश बाबू आत्मज प्रकाश बाबू") == "महेश बाबू"
    assert clean_name("Dr. K. Srinivas Goud") in ("Srinivas Goud", "K Srinivas Goud")


def test_area_unit_conversion_standardization():
    """H07, H08: Area converted to square metres and hectares."""
    sqm, ha, unit = convert_area(2.0, "hectare")
    assert sqm == 20000.0
    assert ha == 2.0

    sqm, ha, unit = convert_area(1.0, "acre")
    assert round(sqm, 1) == 4046.9
    assert round(ha, 4) == 0.4047

    sqm, ha, unit = convert_area(40.0, "guntha")
    assert round(ha, 2) == 0.40


def test_fuzzy_matching_gazetteer():
    """H22, H23: Gazetteer and fuzzy string matching."""
    match = fuzzy_match("Rampoor", ["Rampur", "Gopalapuram", "Venkatapuram"])
    assert match is not None
    assert match[0] == "Rampur"
    assert match[1] >= 0.75


def test_multilingual_extraction_english():
    """H01-H20: English land record text extraction with dual schema and source."""
    sample_text = """
    GOVERNMENT OF ANDHRA PRADESH - REVENUE DEPARTMENT
    District: Krishna | State: Andhra Pradesh
    Survey No: 142/1-B
    Khasra No: 512/3
    Khata No: 884
    Patta No: P-2024-991
    Owner Name: Shri Ramesh Naidu s/o Venkata Naidu
    Total Area: 2.50 Hectares
    Village: Rampur
    Tehsil: Vijayawada
    Classification: Agricultural
    Land Use: Single Crop
    Deed Registration No: DOC-KRI-2023-994
    Registration Date: 15/01/2023
    Mutation Number: MUT-2023-112
    Mutation Date: 20/03/2023
    Mutation Status: Approved
    Encumbrance Status: Clear
    """
    res = extractor.extract_all(sample_text, ocr_confidence=0.95, context_state="Andhra Pradesh", context_district="Krishna")
    fields = res["fields"]

    assert fields["survey_no"]["normalized_value"] == "142/1-B"
    assert fields["khasra_no"]["normalized_value"] == "512/3"
    assert fields["khata_no"]["normalized_value"] == "884"
    assert fields["patta_no"]["normalized_value"] == "P-2024-991"
    assert "Ramesh Naidu" in fields["owner_name"]["normalized_value"]
    assert fields["area_value"]["normalized_value"] == 2.50
    assert fields["area_ha"]["normalized_value"] == 2.50
    assert fields["village"]["normalized_value"] == "Rampur"
    assert fields["district"]["normalized_value"] == "Krishna"
    assert fields["state"]["normalized_value"] == "Andhra Pradesh"
    assert fields["land_classification"]["normalized_value"] == "Agricultural"
    assert fields["registration_date"]["normalized_value"] == "2023-01-15"
    assert fields["mutation_date"]["normalized_value"] == "2023-03-20"
    assert fields["mutation_status"]["normalized_value"] == "Approved"
    assert fields["encumbrance_status"]["normalized_value"] == "Clear"

    # I01-I15: Confidence scoring checks
    assert res["overall_confidence"] >= 0.85
    assert res["badge_color"] in ["green", "yellow"]
    assert not res["requires_review"]


def test_multilingual_extraction_hindi():
    """H24: Multilingual extraction with Hindi aliases."""
    hindi_text = """
    भू-अभिलेख विभाग
    खसरा संख्या: 89/2
    खाता संख्या: 441
    भूस्वामी: श्री महेश शर्मा
    रकबा: 1.80 हेक्टेयर
    ग्राम: रामपुर
    तहसील: मेदक
    जिला: Medak
    राज्य: Telangana
    """
    res = extractor.extract_all(hindi_text, ocr_confidence=0.90, context_state="Telangana", context_district="Medak")
    fields = res["fields"]

    assert fields["khasra_no"]["normalized_value"] == "89/2"
    assert fields["khata_no"]["normalized_value"] == "441"
    assert "महेश शर्मा" in fields["owner_name"]["normalized_value"]
    assert fields["area_value"]["normalized_value"] == 1.80
    assert fields["village"]["normalized_value"] == "Rampur"


def test_confidence_threshold_badges():
    """I06-I09: Verification of threshold badge colors."""
    assert get_confidence_badge(0.95)[0] == "green"
    assert get_confidence_badge(0.78)[0] == "yellow"
    assert get_confidence_badge(0.55)[0] == "orange"
    assert get_confidence_badge(0.25)[0] == "red"


def test_land_rule_validator_area_mismatch_calculator():
    """J21: GIS vs Document area mismatch calculator (<10% pass, 10-25% warn, >25% fail)."""
    # Create 2.0 ha polygon
    poly_2ha = rectangle(16.5, 80.5, area_ha=2.0)

    # 1. Matching area (2.0 ha text vs 2.0 ha GIS) -> Pass
    res_pass = validator.validate_extraction(
        {"fields": {"area_value": {"normalized_value": 2.0}, "area_ha": {"normalized_value": 2.0}}},
        parcel_geometry=poly_2ha,
    )
    j21_pass = next(r for r in res_pass["results"] if r["rule_id"] == "J21")
    assert j21_pass["status"] == "passed"

    # 2. Moderate mismatch (1.75 ha text vs 2.0 ha GIS = ~14% diff) -> Warning
    res_warn = validator.validate_extraction(
        {"fields": {"area_value": {"normalized_value": 1.75}, "area_ha": {"normalized_value": 1.75}}},
        parcel_geometry=poly_2ha,
    )
    j21_warn = next(r for r in res_warn["results"] if r["rule_id"] == "J21")
    assert j21_warn["status"] == "warning"

    # 3. Critical mismatch (1.0 ha text vs 2.0 ha GIS = 100% diff) -> Critical Failure
    res_fail = validator.validate_extraction(
        {"fields": {"area_value": {"normalized_value": 1.0}, "area_ha": {"normalized_value": 1.0}}},
        parcel_geometry=poly_2ha,
    )
    j21_fail = next(r for r in res_fail["results"] if r["rule_id"] == "J21")
    assert j21_fail["status"] == "failed"
    assert j21_fail["severity"] == "critical"


def test_land_rule_validator_joint_ownership_shares():
    """J14, J15: Joint ownership shares sum to 100%."""
    # 50% + 50% = 100% -> Pass
    valid_shares = [
        {"owner": "Ramesh", "share_pct": 50.0},
        {"owner": "Suresh", "share_pct": 50.0},
    ]
    res_valid = validator.validate_extraction(
        {"fields": {}, "ownership_shares": valid_shares}
    )
    j15_valid = next(r for r in res_valid["results"] if r["rule_id"] == "J15")
    assert j15_valid["status"] == "passed"

    # 40% + 50% = 90% -> Fail
    invalid_shares = [
        {"owner": "Ramesh", "share_pct": 40.0},
        {"owner": "Suresh", "share_pct": 50.0},
    ]
    res_invalid = validator.validate_extraction(
        {"fields": {}, "ownership_shares": invalid_shares}
    )
    j15_invalid = next(r for r in res_invalid["results"] if r["rule_id"] == "J15")
    assert j15_invalid["status"] == "failed"


def test_land_rule_validator_date_order():
    """J16: Mutation date cannot precede registration date."""
    # Mutation 2023-01-01 before Registration 2023-05-01 -> Fail
    extracted = {
        "fields": {
            "registration_date": {"normalized_value": "2023-05-01"},
            "mutation_date": {"normalized_value": "2023-01-01"},
        }
    }
    res = validator.validate_extraction(extracted)
    j16 = next(r for r in res["results"] if r["rule_id"] == "J16")
    assert j16["status"] == "failed"


def test_land_rule_validator_duplicate_detection():
    """J12, J13: Duplicate survey number within project/village."""
    existing = [
        {"id": 1, "survey_no": "104/A", "village": "Rampur"},
        {"id": 2, "survey_no": "105/B", "village": "Rampur"},
    ]
    extracted = {
        "fields": {
            "survey_no": {"normalized_value": "104/A"},
            "village": {"normalized_value": "Rampur"},
        }
    }
    res = validator.validate_extraction(extracted, existing_parcels=existing)
    j12 = next(r for r in res["results"] if r["rule_id"] == "J12")
    assert j12["status"] == "failed"
    assert "Duplicate parcel detected" in j12["message"]


def test_end_to_end_extraction_api(client, auth):
    """End-to-end integration test of Document Extraction and Parcel Validation APIs."""
    headers = auth("central")
    # 1. Get projects
    r = client.get("/api/projects", headers=headers)
    assert r.status_code == 200
    res_data = r.json()
    projects = res_data["items"] if isinstance(res_data, dict) and "items" in res_data else res_data
    assert len(projects) > 0
    p = projects[0]

    # 2. Get project documents
    r_docs = client.get(f"/api/projects/{p['id']}/documents", headers=headers)
    assert r_docs.status_code == 200
    docs = r_docs.json()
    assert len(docs) > 0
    doc = docs[0]

    # 3. Extract and validate document
    r_ext = client.post(
        f"/api/documents/{doc['id']}/extract",
        json={"ocr_confidence": 0.94},
        headers=headers,
    )
    assert r_ext.status_code == 200
    ext_resp = r_ext.json()
    assert "extraction" in ext_resp
    assert "validation" in ext_resp
    assert ext_resp["extraction"]["overall_confidence"] > 0.0
    assert len(ext_resp["validation"]["results"]) > 0

    # 4. Get document extraction details
    r_get_ext = client.get(f"/api/documents/{doc['id']}/extraction", headers=headers)
    assert r_get_ext.status_code == 200
    saved_ext = r_get_ext.json()
    assert saved_ext["has_extraction"] is True
    assert "survey_no" in saved_ext["fields"]
    assert len(saved_ext["validation_records"]) > 0

    # 5. Validate parcel rules
    r_parcels = client.get(f"/api/projects/{p['id']}/parcels", headers=headers)
    assert r_parcels.status_code == 200
    parcels = r_parcels.json()
    if parcels:
        target_parcel = parcels[0]
        r_val = client.post(f"/api/parcels/{target_parcel['id']}/validate", headers=headers)
        assert r_val.status_code == 200
        val_data = r_val.json()
        assert "validation_status" in val_data
        assert "area_mismatch_pct" in val_data

        r_get_val = client.get(f"/api/parcels/{target_parcel['id']}/validation", headers=headers)
        assert r_get_val.status_code == 200
        assert len(r_get_val.json()["records"]) > 0
