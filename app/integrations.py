"""Integration gateway. Adapters here are MOCKS with deterministic output.

To connect a real land-records or cadastral service, implement the same `lookup` /
`area_ha` methods against the real API and return that class from the two `get_*`
functions. Nothing else in the codebase needs to change.
"""
import hashlib
import random
from typing import Optional

from .geo import geometry_area_ha

_NAMES = [
    "Ramesh Naidu", "Lakshmi Devi", "Suresh Reddy", "Anitha Kumari", "Venkata Rao", "Sujatha Rani",
    "Mahesh Babu", "Padmavathi", "Srinivas Goud", "Kavitha Sharma", "Ravi Teja", "Bhavani Prasad",
    "Harish Chandra", "Swapna Latha", "Narasimha Murthy", "Vijaya Lakshmi", "Prakash Rao", "Meena Kumari",
]
LAND_TYPES = ["agricultural", "agricultural", "agricultural", "residential", "barren", "commercial"]


def _rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


class MockLandRecords:
    """Stands in for a state land-records / DILRMP-style lookup by survey number."""

    name = "mock-land-records"

    def lookup(self, state: str, district: str, village: str, survey_no: str) -> dict:
        r = _rng(state, district, village, survey_no)
        return {
            "source": self.name,
            "survey_no": survey_no,
            "owner_name": r.choice(_NAMES),
            "area_ha": round(r.uniform(1.0, 7.0), 2),
            "land_type": r.choice(LAND_TYPES),
            "encumbrance": r.choices(["clear", "mortgage", "dispute"], weights=[0.80, 0.12, 0.08])[0],
        }


class MockCadastral:
    """Stands in for a cadastral map service; here it measures the stored parcel polygon."""

    name = "mock-cadastral"

    def area_ha(self, geometry: Optional[dict]) -> Optional[float]:
        a = geometry_area_ha(geometry)
        return round(a, 2) if a is not None else None


def get_land_records() -> MockLandRecords:
    return MockLandRecords()


def get_cadastral() -> MockCadastral:
    return MockCadastral()


def verify_parcel(parcel, project) -> dict:
    """Cross-check a parcel against land records and the cadastral polygon."""
    rec = get_land_records().lookup(project.state, project.district, parcel.village, parcel.survey_no)
    cad_area = get_cadastral().area_ha(parcel.geometry)
    checks = []

    same_owner = rec["owner_name"].strip().lower() == (parcel.owner_name or "").strip().lower()
    checks.append(
        {"name": "Owner matches land record", "status": "ok" if same_owner else "fail",
         "detail": f"record: {rec['owner_name']}; entered: {parcel.owner_name or '-'}"}
    )
    rec_gap = abs(rec["area_ha"] - parcel.area_ha) / max(rec["area_ha"], 0.01)
    checks.append(
        {"name": "Area matches land record (within 5%)", "status": "ok" if rec_gap <= 0.05 else "fail",
         "detail": f"record: {rec['area_ha']} ha; entered: {parcel.area_ha:.2f} ha ({rec_gap:.0%} apart)"}
    )
    if cad_area is not None:
        cad_gap = abs(cad_area - parcel.area_ha) / max(cad_area, 0.01)
        checks.append(
            {"name": "Area matches cadastral polygon (within 5%)", "status": "ok" if cad_gap <= 0.05 else "fail",
             "detail": f"polygon: {cad_area} ha; entered: {parcel.area_ha:.2f} ha ({cad_gap:.0%} apart)"}
        )
    checks.append(
        {"name": "Encumbrance", "status": "ok" if rec["encumbrance"] == "clear" else "warn",
         "detail": f"record shows: {rec['encumbrance']}"}
    )
    return {
        "parcel_id": parcel.id,
        "sources": [get_land_records().name, get_cadastral().name],
        "verified": all(c["status"] != "fail" for c in checks),
        "checks": checks,
        "record": rec,
    }
