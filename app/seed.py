"""Synthetic demo data. Nothing here is real: names, survey numbers, coordinates and dates are generated.

Run standalone with `python -m app.seed --reset`, or let the app seed an empty database on first start.
Projects are spread across all lifecycle stages with a simulated history so every screen has something to show.
"""
import math
import random
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, geo, integrations
from .models import (
    Award, Compensation, Event, Family, Milestone, Notification, Parcel, Project, RiskScore, User, utcnow,
)
from .routers.documents import write_version
from .routers.projects import STATE_CODES
from .security import hash_password
from .services import risk_service
from .workflow import (
    DEFAULT_DAYS, STAGE_KEYS, STAGE_LABELS, STALL_REASONS, build_milestones, plan, plan_keys, template_key_for_state,
)

DEMO_PASSWORD = "demo1234"
SEED = 2026

# (state, district, lat, lon, number of projects)
DISTRICTS = [
    ("Andhra Pradesh", "Krishna", 16.61, 80.72, 5),
    ("Andhra Pradesh", "Anantapur", 14.68, 77.60, 3),
    ("Andhra Pradesh", "Visakhapatnam", 17.69, 83.22, 3),
    ("Andhra Pradesh", "Kurnool", 15.83, 78.04, 2),
    ("Andhra Pradesh", "Nellore", 14.44, 79.99, 2),
    ("Andhra Pradesh", "Guntur", 16.31, 80.44, 3),
    ("Telangana", "Rangareddy", 17.25, 78.20, 4),
    ("Telangana", "Medak", 17.99, 78.26, 2),
    ("Telangana", "Warangal", 17.98, 79.59, 3),
    ("Telangana", "Nalgonda", 17.05, 79.27, 2),
    ("Karnataka", "Tumakuru", 13.34, 77.10, 3),
    ("Karnataka", "Ballari", 15.14, 76.92, 2),
    ("Karnataka", "Belagavi", 15.85, 74.50, 1),
    ("Karnataka", "Kolar", 13.14, 78.13, 1),
]

VILLAGES = [
    "Rampur", "Venkatapuram", "Chinnapalem", "Gopalapuram", "Ramannapeta", "Kothapalli", "Devarapalli",
    "Lingapuram", "Narsapur", "Peddapadu", "Mallavaram", "Sitarampuram", "Thimmapur", "Yerragunta",
]
FIRST = ["Ramesh", "Lakshmi", "Suresh", "Anitha", "Venkata", "Sujatha", "Mahesh", "Padma", "Srinivas", "Kavitha",
         "Ravi", "Bhavani", "Harish", "Swapna", "Narasimha", "Vijaya", "Prakash", "Meena", "Sai", "Durga"]
LAST = ["Naidu", "Devi", "Reddy", "Kumari", "Rao", "Rani", "Babu", "Goud", "Sharma", "Teja", "Prasad", "Murthy", "Yadav"]

TYPE_WEIGHTS = {"highway": 30, "railway": 15, "solar_park": 12, "irrigation": 15, "urban": 12, "industrial_corridor": 16}
PARCEL_RANGE = {"highway": (6, 12), "railway": (5, 10), "solar_park": (4, 8), "irrigation": (5, 10),
                "urban": (4, 8), "industrial_corridor": (4, 9)}
STAGE_WEIGHTS = {"proposal": 3, "scrutiny": 4, "approval": 4, "notification": 4, "survey_objections": 5,
                 "award": 4, "compensation": 5, "possession": 3, "rr": 3, "closeout": 3}
STAGE_STATE_USER = {"Andhra Pradesh": "state_ap", "Telangana": "state_ts", "Karnataka": "state_ka"}

NOTES = {
    "proposal": ["Preliminary alignment shared with the revenue department."],
    "scrutiny": ["Digital scrutiny queries raised on the land plan; agency asked to resubmit."],
    "approval": ["File is with the finance department for concurrence."],
    "notification": ["Draft notification vetted by the district legal cell."],
    "survey_objections": ["Joint measurement completed for most survey numbers.", "Landowners submitted objections on boundary records."],
    "award": ["Award statement under review by the collector."],
    "compensation": ["Landowners asked for a revised solatium rate.", "Treasury release awaited for the second instalment."],
    "possession": ["Possession notices served; two owners have standing crops."],
    "rr": ["Resettlement colony layout approved by the district committee."],
}
STALL_BY_STAGE = {
    "proposal": ["documentation"], "scrutiny": ["documentation", "admin_bottleneck"],
    "approval": ["clearance_pending", "funding_gap"], "notification": ["admin_bottleneck", "court_stay"],
    "survey_objections": ["protest", "court_stay"], "award": ["court_stay", "admin_bottleneck"],
    "compensation": ["compensation_dispute", "funding_gap"], "possession": ["protest", "court_stay"],
    "rr": ["rr_dispute", "funding_gap"],
}


def _agency(state: str, ptype: str) -> str:
    if ptype == "highway":
        return "NHAI"
    if ptype == "railway":
        return "South Western Railway" if state == "Karnataka" else "South Central Railway"
    if ptype == "industrial_corridor":
        return {"Andhra Pradesh": "APIIC", "Telangana": "TSIIC", "Karnataka": "KIADB"}[state]
    if ptype == "solar_park":
        return f"{STATE_CODES[state]} Renewable Energy Development Corp"
    if ptype == "irrigation":
        return f"{state} Water Resources Department"
    return f"{state} Urban Development Authority"


def _name(rng: random.Random, ptype: str, district: str) -> str:
    return {
        "highway": f"NH-{rng.choice([16, 44, 65, 167, 544, 716])} four-laning, {district} section",
        "railway": f"{district} rail line doubling",
        "solar_park": f"{district} ultra-mega solar park",
        "irrigation": f"{district} lift irrigation scheme",
        "urban": f"{district} planned township, phase {rng.randint(1, 3)}",
        "industrial_corridor": f"{district} industrial corridor, node {rng.randint(1, 4)}",
    }[ptype]


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def create_users(db: Session) -> None:
    def add(username, full_name, role, state=None, district=None, agency=None):
        db.add(User(username=username, password_hash=hash_password(DEMO_PASSWORD), full_name=full_name,
                    role=role, state=state, district=district, agency=agency))

    add("central", "Central Nodal Officer, DoLR", "central")
    add("auditor", "Internal Auditor", "auditor")
    add("state_ap", "State Nodal Officer, Andhra Pradesh", "state", "Andhra Pradesh")
    add("state_ts", "State Nodal Officer, Telangana", "state", "Telangana")
    add("state_ka", "State Nodal Officer, Karnataka", "state", "Karnataka")
    for state, district, *_ in DISTRICTS:
        add(f"dist_{district.lower()}", f"District Collector's Office, {district}", "district", state, district)
    for district, state in (("Krishna", "Andhra Pradesh"), ("Rangareddy", "Telangana"), ("Tumakuru", "Karnataka")):
        add(f"field_{district.lower()}", f"Field Surveyor, {district}", "field", state, district)
    add("agency_nhai", "NHAI Project Cell", "agency", agency="NHAI")
    add("agency_apiic", "APIIC Land Cell", "agency", agency="APIIC")
    add("agency_scr", "South Central Railway Land Cell", "agency", agency="South Central Railway")
    db.flush()


def _make_parcels(db: Session, rng: random.Random, project: Project, lat: float, lon: float, trouble: float) -> list[Parcel]:
    lo, hi = PARCEL_RANGE[project.project_type]
    n = rng.randint(lo, hi)
    villages = rng.sample(VILLAGES, 3)
    base_lat, base_lon = lat + rng.uniform(-0.12, 0.12), lon + rng.uniform(-0.12, 0.12)
    angle = rng.uniform(0, math.pi)
    step = 0.0068
    used, parcels = set(), []
    for i in range(n):
        village = villages[i * 3 // n]
        survey = f"{rng.randint(11, 890)}/{rng.choice('ABC')}"
        while survey in used:
            survey = f"{rng.randint(11, 890)}/{rng.choice('ABC')}"
        used.add(survey)
        rec = integrations.get_land_records().lookup(project.state, project.district, village, survey)
        owner, area, poly_scale = rec["owner_name"], rec["area_ha"], 1.0
        if rng.random() < 0.10:  # deliberate data-quality problems for the verification demo
            kind = rng.choice(["owner", "area", "polygon"])
            if kind == "owner":
                owner = _person(rng)
            elif kind == "area":
                area = round(area * 1.18, 2)
            else:
                poly_scale = 0.78
        clat = base_lat + i * step * math.sin(angle) + rng.uniform(-0.0015, 0.0015)
        clon = base_lon + i * step * math.cos(angle) + rng.uniform(-0.0015, 0.0015)
        parcels.append(
            Parcel(
                project_id=project.id, survey_no=survey, village=village, owner_name=owner, area_ha=area,
                land_type=rec["land_type"], status="proposed",
                disputed=rng.random() < 0.04 + 0.18 * trouble,
                geometry=geo.rectangle(clat, clon, area * poly_scale, aspect=rng.uniform(1.1, 2.0)),
            )
        )
    db.add_all(parcels)
    db.flush()
    return parcels


def _seed_project(db: Session, rng: random.Random, state: str, district: str, lat: float, lon: float, ptype: str) -> Project:
    now = utcnow()
    trouble = min(1.0, max(0.0, rng.betavariate(2, 3)))
    template = template_key_for_state(state)
    keys = plan_keys(template)
    weights = [STAGE_WEIGHTS[k] for k in keys]
    cur_idx = rng.choices(range(len(keys)), weights=weights)[0]
    cur = keys[cur_idx]
    completed = cur == "closeout"
    planned = {s["key"]: s["days"] for s in plan(template)}

    stalled = (not completed) and cur != "proposal" and rng.random() < 0.10 + 0.55 * max(0.0, trouble - 0.45)
    durations = []
    for k in keys[:cur_idx]:
        factor = min(2.6, max(0.4, rng.lognormvariate(math.log(0.85 + 0.6 * trouble), 0.25)))
        durations.append(max(1.0, planned[k] * factor))
    if completed:
        elapsed = rng.uniform(5, 90)  # days since completion
    elif stalled:
        elapsed = planned[cur] * rng.uniform(0.9, 2.1)
    else:
        elapsed = planned[cur] * rng.uniform(0.12, 0.85 + 1.3 * trouble)
    total_days = sum(durations) + elapsed
    start = now - timedelta(days=total_days)

    project = Project(
        code="TMP", name=_name(rng, ptype, district), project_type=ptype, agency=_agency(state, ptype),
        state=state, district=district, template_key=template, stage=cur, stage_entered_at=now - timedelta(days=elapsed),
        status="completed" if completed else "active", created_at=start,
        legal_disputes=int(trouble * 4 * rng.random() + (1 if trouble > 0.6 and rng.random() < 0.5 else 0)),
        approvals_pending=int(trouble * (4 if cur_idx <= 2 else 1.5) * rng.random()),
        avg_response_days=round(3 + 22 * trouble * rng.random() + rng.uniform(0, 3), 1),
    )
    db.add(project)
    db.flush()
    project.code = f"LA-{STATE_CODES[state]}-{start.year}-{project.id:04d}"

    # ---- milestones with simulated actual dates
    build_milestones(db, project, start)
    milestones = {m.stage: m for m in db.scalars(select(Milestone).where(Milestone.project_id == project.id))}
    dist_user = f"dist_{district.lower()}"
    state_user = STAGE_STATE_USER[state]
    cursor = start
    db.add(Event(project_id=project.id, kind="created", stage="proposal", actor="agency", at=start,
                 detail=f"Proposal created by {project.agency}"))
    for k, d in zip(keys[:cur_idx], durations):
        end = cursor + timedelta(days=d)
        ms = milestones[k]
        ms.actual_start, ms.actual_end = cursor, end
        nxt = keys[keys.index(k) + 1]
        actor = state_user if k in ("proposal", "scrutiny", "approval") else dist_user
        db.add(Event(project_id=project.id, kind="stage_change", stage=nxt, actor=actor, at=end,
                     detail=f"{STAGE_LABELS[k]} -> {STAGE_LABELS[nxt]}"))
        cursor = end
    if cur in milestones:
        milestones[cur].actual_start = project.stage_entered_at
        if completed:
            milestones[cur].actual_end = project.stage_entered_at
    if project.planned_completion is None:
        project.planned_completion = (start + timedelta(days=sum(planned.values()) + rng.randint(0, 60))).date()

    # ---- notes, disputes and stall history
    if not completed:
        for text in rng.sample(NOTES.get(cur, []), k=min(len(NOTES.get(cur, [])), rng.choice([0, 1, 1, 2]))):
            db.add(Event(project_id=project.id, kind="note", stage=cur, actor=dist_user, at=now - timedelta(days=rng.uniform(1, max(2, elapsed))),
                         detail=text))
    if project.legal_disputes:
        db.add(Event(project_id=project.id, kind="dispute", stage=cur, actor=dist_user,
                     at=now - timedelta(days=rng.uniform(5, max(6, min(total_days, 120)))),
                     detail=f"{project.legal_disputes} writ petition(s) filed by affected landowners at the High Court"))
    if stalled:
        reasons = STALL_BY_STAGE.get(cur, ["other"])
        if project.legal_disputes >= 2 and "court_stay" not in reasons:
            reasons = reasons + ["court_stay"]
        reason = rng.choice(reasons)
        since = now - timedelta(days=min(elapsed * 0.9, rng.uniform(12, 80)))
        note = {
            "court_stay": "Stay order granted on further proceedings; counsel to file for vacation.",
            "funding_gap": "Budget release pending from the state finance department.",
            "protest": "Local protest at the site; talks with village representatives ongoing.",
            "compensation_dispute": "Owners rejected the assessed rate and demand revision.",
            "clearance_pending": "Awaiting forest and environment clearance.",
            "documentation": "Revenue records incomplete for several survey numbers.",
            "admin_bottleneck": "File pending with the competent authority.",
            "rr_dispute": "Displaced families dispute the resettlement site.",
        }.get(reason, "")
        project.status, project.stall_reason, project.stall_note, project.stalled_since = "stalled", reason, note, since
        db.add(Event(project_id=project.id, kind="stalled", stage=cur, actor=dist_user, at=since,
                     detail=f"{STALL_REASONS[reason]}. {note}".strip()))

    # ---- parcels and stage-dependent land records
    parcels = _make_parcels(db, rng, project, lat, lon, trouble)
    total_area = sum(p.area_ha for p in parcels)
    project.proposed_area_ha = round(total_area * rng.uniform(1.0, 1.1), 2)
    project.estimated_cost_cr = round(project.proposed_area_ha * rng.uniform(8, 40), 1)
    stage_i = STAGE_KEYS.index(cur)
    at_least = lambda s: stage_i >= STAGE_KEYS.index(s)  # noqa: E731
    step_dates = {k: (milestones[k].actual_start or start).date() for k in keys if k in milestones and milestones[k].actual_start}

    if at_least("survey_objections"):
        for p in parcels:
            p.status = "notified"
    if at_least("notification") and (cur != "notification" or rng.random() < 0.5):
        db.add(Notification(project_id=project.id, kind="preliminary", ref_no=f"{project.code}/N1",
                            issued_on=step_dates.get("notification", start.date()), area_ha=round(total_area, 2)))
    if at_least("award") and (cur != "award" or rng.random() < 0.4):
        rate = rng.uniform(0.4, 1.4)
        db.add(Award(project_id=project.id, award_no=f"{project.code}/AW1", declared_on=step_dates.get("award", start.date()),
                     total_area_ha=round(total_area, 2), total_amount_cr=round(total_area * rate, 2)))
    if at_least("compensation"):
        rate = rng.uniform(0.4, 1.4)
        for p in parcels:
            p.status = "awarded"
        if cur == "compensation":
            frac = min(1.0, max(0.0, elapsed / planned["compensation"] * rng.uniform(0.6, 1.1) - 0.25 * trouble))
            if stalled:
                frac *= 0.5
        else:
            frac = 1.0
        for p in parcels:
            assessed = round(p.area_ha * rate, 4)
            if frac >= 0.97 or rng.random() < frac:
                paid = assessed
            else:
                paid = round(assessed * rng.uniform(0, max(frac, 0.05)), 4)
            db.add(Compensation(project_id=project.id, parcel_id=p.id, assessed_cr=assessed, disbursed_cr=paid,
                                assessed_on=step_dates.get("compensation", start.date()),
                                last_disbursed_on=(now - timedelta(days=rng.uniform(1, 30))).date() if paid else None))
            if paid >= assessed - 1e-9:
                p.status = "compensated"
    if at_least("possession"):
        share = 1.0 if at_least("rr") else min(1.0, elapsed / planned["possession"] * rng.uniform(0.5, 1.0))
        for p in parcels:
            p.status = "compensated" if p.status == "awarded" else p.status
            if p.status == "compensated" and (share >= 1.0 or rng.random() < share):
                p.status = "possessed"
                p.possession_date = (now - timedelta(days=rng.uniform(1, 60))).date()

    # ---- affected families and R&R progress
    families = []
    for p in parcels:
        for _ in range(rng.randint(1, 3)):
            displaced = rng.random() < (0.35 if p.land_type in ("residential", "commercial") else 0.06)
            families.append(Family(project_id=project.id, parcel_id=p.id, head_name=_person(rng),
                                   members=rng.randint(2, 7), displaced=displaced, rr_status="pending"))
    if at_least("possession") and not any(f.displaced for f in families):
        for f in families[:2]:
            f.displaced = True
    if at_least("rr"):
        progress = 1.0 if cur == "closeout" else min(1.0, elapsed / planned["rr"] * rng.uniform(0.4, 1.0))
        for f in families:
            if not f.displaced:
                continue
            r = rng.random()
            f.rr_status = ("resettled" if r < progress * 0.9 else "allotted" if r < progress * 1.2
                           else "package_approved" if r < progress * 1.6 + 0.2 else "pending")
            if cur == "closeout":
                f.rr_status = "resettled"
    elif at_least("possession"):
        for f in families:
            if f.displaced and rng.random() < 0.4:
                f.rr_status = "package_approved"
    db.add_all(families)

    # ---- documents (small text placeholders; the versioning and checksums are real)
    def doc(name, category, note=""):
        data = f"SYNTHETIC DEMO DOCUMENT\nProject: {project.code}\nDocument: {name}\nNo real data.\n".encode()
        write_version(db, project.id, name, category, f"{category}.txt", data, dist_user, note)

    if cur != "proposal" or rng.random() < 0.7:
        doc("Proposal report", "proposal_report")
        if rng.random() < 0.2:
            doc("Proposal report", "proposal_report", "Revised after scrutiny comments")
    if at_least("approval") or (cur == "scrutiny" and rng.random() < 0.8):
        have = ["land_plan", "cost_estimate", "environment_clearance", "social_impact_assessment"]
        if cur == "scrutiny":
            have = rng.sample(have, rng.randint(1, 4))
        elif rng.random() < 0.2:
            have.remove(rng.choice(have))
        for c in have:
            doc(c.replace("_", " ").capitalize(), c)
    if at_least("survey_objections"):
        doc("Notification copy", "notification_copy")
    if at_least("compensation"):
        doc("Award copy", "award_copy")

    audit.log(db, "seed", "seed_project", "project", project.id, {"code": project.code})
    return project


def _backfill_risk_history(db: Session, projects: list[Project], rng: random.Random) -> None:
    """Synthetic monthly risk snapshots for the past few months, so the trend chart has a trend to
    show on a freshly seeded database instead of a single point. A simple heuristic anchor (not the
    ML model), tagged 'seed-backfill' in model_version so it's never mistaken for a real scoring run.
    Inserted BEFORE the real current score_projects() call so the true current row keeps the highest
    id and stays the one every "latest risk" lookup (which orders by id) actually returns.
    """
    now = utcnow()
    for p in projects:
        anchor = min(0.95, max(0.05, 0.12 + 0.07 * p.legal_disputes + 0.05 * p.approvals_pending + (0.25 if p.status == "stalled" else 0)))
        walk = max(0.05, min(0.95, anchor + rng.uniform(-0.15, 0.15)))
        months = rng.randint(3, 5)
        for i in range(months, 0, -1):
            walk = min(0.95, max(0.05, 0.7 * walk + 0.3 * anchor + rng.uniform(-0.08, 0.08)))
            db.add(RiskScore(
                project_id=p.id, scored_at=now - timedelta(days=30 * i + rng.randint(0, 8)), stage=p.stage,
                probability=round(walk, 4), score=int(round(walk * 100)), category=risk_service.categorize(walk),
                model_version="seed-backfill", features={}, drivers=[], recommendations=[], stage_profile=[],
            ))
    db.flush()


def seed(db: Session) -> int:
    rng = random.Random(SEED)
    create_users(db)

    types = list(TYPE_WEIGHTS)
    made = []
    for state, district, lat, lon, count in DISTRICTS:
        for _ in range(count):
            ptype = rng.choices(types, weights=[TYPE_WEIGHTS[t] for t in types])[0]
            made.append(_seed_project(db, rng, state, district, lat, lon, ptype))
    db.flush()

    risk_service.active_model(db)
    active = [p for p in made if p.status != "completed"]
    _backfill_risk_history(db, active, rng)
    risk_service.score_projects(db, active)
    audit.log(db, "seed", "seed_complete", "system", "-", {"projects": len(made)})
    db.commit()
    return len(made)


if __name__ == "__main__":
    from .db import Base, SessionLocal, engine

    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        n = seed(session)
    print(f"Seeded {n} synthetic projects. Log in as 'central' / '{DEMO_PASSWORD}'.")
