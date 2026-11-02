"""Build a messy, multi-tenant prior authorization history and load it into DuckDB.

The structural path of a Medicare GLP-1 case comes from pa_model.routing.
Timing and approval chances come from assumptions/behavior.yml. Those chances
are synthetic. They are not a payer's real operating statistics.
"""

from __future__ import annotations

import csv
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import yaml

from pa_model.reference import (
    CLASS_DRUGS,
    CLINICS,
    DENIAL_BY_PATH,
    DIAGNOSES,
    DRUGS,
    DX_WEIGHTS,
    FEATURED_CLINIC_ID,
    FIRST_NAMES,
    FREE_TEXT_BY_CODE,
    LAST_NAMES,
    PAYERS,
    SECONDARY_FOR_WEIGHT,
    SPECIALTIES,
)
from pa_model.routing import indication_match, route_case

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def load_behavior() -> dict:
    with (ROOT / "assumptions" / "behavior.yml").open() as fh:
        return yaml.safe_load(fh)


def weighted(rng: random.Random, items, weights):
    total = sum(weights)
    pick = rng.random() * total
    upto = 0.0
    for item, weight in zip(items, weights):
        upto += weight
        if pick <= upto:
            return item
    return items[-1]


def hours_draw(rng: random.Random, median: float, sigma: float, lo: float = 0.3, hi: float = 24 * 28) -> float:
    value = rng.lognormvariate(math.log(max(median, 0.2)), sigma)
    return min(hi, max(lo, value))


def add_hours(moment: datetime, amount: float) -> datetime:
    return moment + timedelta(hours=amount)


def npi_is_valid(npi: str) -> bool:
    """CMS NPI check. The 80840 prefix contributes a constant 24 to the Luhn sum."""
    if len(npi) != 10 or not npi.isdigit():
        return False
    total = 24
    for index, char in enumerate(reversed(npi)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def fake_npi(rng: random.Random) -> str:
    while True:
        digits = str(rng.choice([1, 2])) + "".join(str(rng.randrange(10)) for _ in range(9))
        if not npi_is_valid(digits):
            return digits


def age_on(birth: datetime, moment: datetime) -> int:
    return moment.year - birth.year - ((moment.month, moment.day) < (birth.month, birth.day))


def p_approve(cfg: dict, path: str, indication: str, drug_class: str, reauth: bool, expedited: bool) -> float:
    approval = cfg["approval"]
    if path == "part_d_exclusion":
        chance = approval["part_d_exclusion"]
    elif path == "glp1_bridge":
        chance = approval["glp1_bridge"]
    else:
        block = approval[path]
        if indication == "off_label":
            chance = block["off_label"]
        else:
            chance = block["on_label"][drug_class]
    adj = cfg["adjustments"]
    if reauth:
        chance += adj["reauth"]
    if expedited:
        chance += adj["expedited"]
    return min(adj["cap"], max(adj["floor"], chance))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {}
            for key in fieldnames:
                value = row.get(key, "")
                if value is None:
                    value = ""
                elif isinstance(value, datetime):
                    value = value.strftime("%Y-%m-%d %H:%M:%S")
                out[key] = value
            writer.writerow(out)


def fmt(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class Builder:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rng = random.Random(cfg["seed"])
        self.start = datetime.fromisoformat(cfg["window_start"])
        self.end = datetime.fromisoformat(cfg["window_end"])
        self.as_of = datetime.fromisoformat(cfg["as_of"])
        self.drugs = {d["drug_id"]: d for d in DRUGS}
        self.payers = {p["payer_id"]: p for p in PAYERS}
        self.event_n = 0
        self.patients: list[dict] = []
        self.prescribers: list[dict] = []
        self.cases: list[dict] = []
        self.events: list[dict] = []
        self.patient_drugs: dict[str, set[str]] = {}
        self.sigma = cfg["timing"]["sigma"]

    def new_event_id(self) -> str:
        self.event_n += 1
        return f"evt_{self.event_n:08d}"

    def pick_drug(self, clinic: dict) -> dict:
        drug_class = weighted(self.rng, list(clinic["class_mix"]), list(clinic["class_mix"].values()))
        if drug_class == "biologic":
            pool = CLASS_DRUGS["biologic"][clinic["clinic_id"]]
        else:
            pool = CLASS_DRUGS[drug_class]
        return self.drugs[self.rng.choice(pool)]

    def pick_dx(self, drug: dict) -> str:
        pairs = DX_WEIGHTS[drug["drug_id"]]
        return weighted(self.rng, [p[0] for p in pairs], [p[1] for p in pairs])

    def make_dimensions(self) -> None:
        for clinic in CLINICS:
            for index in range(10):
                self.prescribers.append(
                    {
                        "prescriber_id": f"pres_{clinic['clinic_id']}_{index:02d}",
                        "clinic_id": clinic["clinic_id"],
                        "npi": fake_npi(self.rng),
                        "first_name": self.rng.choice(FIRST_NAMES),
                        "last_name": self.rng.choice(LAST_NAMES),
                        "specialty": self.rng.choice(SPECIALTIES[clinic["clinic_id"]]),
                        "npi_is_synthetic": 1,
                    }
                )

    def make_patient(self, clinic_id: str, updated_at: datetime) -> dict:
        birth = datetime(self.rng.randint(1946, 2006), self.rng.randint(1, 12), self.rng.randint(1, 28))
        patient = {
            "patient_id": f"pat_{len(self.patients) + 1:07d}",
            "clinic_id": clinic_id,
            "first_name": self.rng.choice(FIRST_NAMES),
            "last_name": self.rng.choice(LAST_NAMES),
            "birth_date": birth.strftime("%Y-%m-%d"),
            "member_id": f"MBR{self.rng.randrange(10**10):010d}",
            "updated_at": updated_at,
            "is_duplicate_row": 0,
        }
        self.patients.append(patient)
        return patient

    def choose_patient(self, clinic_id: str, drug_id: str, initiated: datetime) -> tuple[dict, int]:
        pool = [p for p in self.patients if p["clinic_id"] == clinic_id and p["is_duplicate_row"] == 0]
        reuse = self.cfg["reauth"]["reuse_patient_probability"]
        if pool and self.rng.random() < reuse:
            patient = self.rng.choice(pool)
        else:
            patient = self.make_patient(clinic_id, initiated)
        seen = self.patient_drugs.setdefault(patient["patient_id"], set())
        reauth = 1 if drug_id in seen else 0
        seen.add(drug_id)
        return patient, reauth

    def channel_for(self, clinic: dict) -> str:
        mix = self.cfg["channel_mix"][clinic["organization_type"]]
        return weighted(self.rng, list(mix), list(mix.values()))

    def stop_plan(self, initiated: datetime, channel: str, path: str) -> str | None:
        if path not in {"commercial_pa", "part_d_pa", "medicaid_pa", "glp1_bridge"}:
            return None
        rules = self.cfg["open_cases"]
        age_days = (self.as_of - initiated).total_seconds() / 86400
        if age_days <= rules["recent_age_days"] and self.rng.random() < rules["recent_probability"]:
            return weighted(
                self.rng,
                ["benefit_check", "pa_not_submitted", "awaiting_info", "awaiting_decision", "awaiting_appeal"],
                [0.12, 0.22, 0.18, 0.32, 0.16],
            )
        if (
            channel == "fax"
            and rules["aged_fax_min_days"] <= age_days <= rules["aged_fax_max_days"]
            and self.rng.random() < rules["aged_fax_probability"]
        ):
            return self.rng.choice(["awaiting_info", "awaiting_decision"])
        return None

    def push(self, case: dict, event_type: str, when: datetime, reason: str | None = None) -> None:
        text = ""
        code = reason or ""
        self.events.append(
            {
                "event_id": self.new_event_id(),
                "case_id": case["case_id"],
                "clinic_id": case["clinic_id"],
                "event_type": event_type,
                "event_at": when,
                "recorded_at": when + timedelta(minutes=self.rng.uniform(3, 80)),
                "denial_reason_code": code,
                "denial_reason_text": text,
                "is_correction": 0,
                "is_duplicate_event": 0,
                "is_late_arrival": 0,
                "event_at_missing": 0,
                "is_chronology_error": 0,
                "source_channel": case["channel"],
            }
        )

    def denial_reason(self, case: dict) -> str:
        path = case["coverage_path"]
        if path == "part_d_exclusion":
            key = "part_d_exclusion"
        elif path == "glp1_bridge":
            key = "glp1_bridge"
        elif case["indication_match"] == "off_label":
            key = "off_label"
        else:
            key = case["drug_class"]
        return self.rng.choice(DENIAL_BY_PATH[key])

    def build_events(self, case: dict, patient: dict) -> None:
        rng = self.rng
        cfg = self.cfg
        timing = cfg["timing"]
        ch = case["channel"]
        lob = case["line_of_business"]
        lob_mult = timing["line_of_business_multiplier"][lob]
        payer_mult = timing["payer_multiplier"].get(case["payer_id"], 1.0)
        speed = lob_mult * payer_mult
        if case["priority"] == "expedited":
            speed *= timing["expedited_multiplier"]
        stop = case["stop_at"]
        t = case["initiated_at"]
        self.push(case, "benefit_check_started", t)
        if stop == "benefit_check":
            return
        t = add_hours(t, hours_draw(rng, timing["benefit_verify"][ch] * max(speed, 0.25), self.sigma))
        self.push(case, "benefit_verified", t)
        path = case["coverage_path"]

        if path == "covered_no_pa":
            self.push(case, "pa_not_required", add_hours(t, hours_draw(rng, 1.2, 0.3)))
            return

        if path == "part_d_exclusion":
            self.build_exclusion(case, t, speed)
            return

        t = add_hours(t, hours_draw(rng, 2.0, 0.35))
        self.push(case, "pa_required", t)
        if stop == "pa_not_submitted":
            return

        abandon = cfg["abandon_before_submit"]["base"]
        if ch == "fax":
            abandon += cfg["abandon_before_submit"]["fax_add"]
        if case["drug_class"] == "glp1_weight":
            abandon += cfg["abandon_before_submit"]["weight_add"]
        if path == "glp1_bridge":
            abandon = cfg["abandon_before_submit"]["bridge"]
        if stop is None and rng.random() < abandon:
            t = add_hours(t, hours_draw(rng, timing["submit_after_pa_required"][ch], self.sigma))
            self.push(case, "abandoned", t)
            return

        pa_required_at = t
        t = add_hours(t, hours_draw(rng, timing["submit_after_pa_required"][ch], self.sigma))
        if path == "glp1_bridge":
            claim_at = pa_required_at + (t - pa_required_at) / 2
            self.push(case, "bridge_claim_denied", claim_at, "redirect_to_bridge")
            self.push(case, "plan_redirect_noted", claim_at + timedelta(minutes=15), "redirect_to_bridge")
        self.push(case, "pa_submitted", t)

        if stop == "awaiting_info" or (
            stop is None
            and rng.random()
            < self.info_probability(case)
        ):
            t = add_hours(t, hours_draw(rng, timing["info_to_request"], self.sigma))
            self.push(case, "info_requested", t, "missing_documentation")
            if stop == "awaiting_info":
                return
            if rng.random() < cfg["abandon_after_info_request"]:
                t = add_hours(t, hours_draw(rng, timing["info_return"] * 0.6, self.sigma))
                self.push(case, "abandoned", t)
                return
            t = add_hours(t, hours_draw(rng, timing["info_return"], self.sigma))
            self.push(case, "info_received", t)

        if stop == "awaiting_decision":
            return

        t = add_hours(t, hours_draw(rng, timing["decision_after_ready"][ch] * speed, self.sigma))
        chance = p_approve(
            cfg,
            path,
            case["indication_match"],
            case["drug_class"],
            case["is_reauthorization"] == 1,
            case["priority"] == "expedited",
        )
        approve = rng.random() < chance
        if stop == "awaiting_appeal":
            approve = False
        if approve:
            self.push(case, "decision_approved", t)
            return
        reason = self.denial_reason(case)
        self.push(case, "decision_denied", t, reason)
        appeal_p = cfg["appeal_given_denial"]["base"]
        if path == "part_d_exclusion":
            appeal_p = cfg["appeal_given_denial"]["exclusion"]
        elif case["indication_match"] == "off_label":
            appeal_p = cfg["appeal_given_denial"]["off_label"]
        if stop != "awaiting_appeal" and rng.random() >= appeal_p:
            return
        t = add_hours(t, hours_draw(rng, timing["appeal_file"], self.sigma))
        self.push(case, "appeal_submitted", t)
        if stop == "awaiting_appeal":
            return
        t = add_hours(t, hours_draw(rng, timing["appeal_decision"] * max(lob_mult, 0.5), self.sigma, hi=24 * 40))
        overturn = cfg["overturn_given_appeal"]["on_label"]
        if path == "glp1_bridge":
            overturn = cfg["overturn_given_appeal"]["bridge"]
        elif path == "part_d_exclusion":
            overturn = cfg["overturn_given_appeal"]["exclusion"]
        elif case["indication_match"] == "off_label":
            overturn = cfg["overturn_given_appeal"]["off_label"]
        if rng.random() < overturn:
            self.push(case, "appeal_approved", t)
        else:
            self.push(case, "appeal_denied", t, reason)

    def info_probability(self, case: dict) -> float:
        info = self.cfg["info_request"]
        if case["coverage_path"] == "glp1_bridge":
            chance = info["glp1_bridge"]
        elif case["coverage_path"] == "part_d_exclusion":
            chance = info["part_d_exclusion"]
        else:
            chance = info[case["drug_class"]]
        if case["channel"] == "fax":
            chance += info["fax_add"]
        return min(chance, 0.85)

    def build_exclusion(self, case: dict, t: datetime, speed: float) -> None:
        rng = self.rng
        timing = self.cfg["timing"]
        # A small share are approved exceptions. They have to be submitted.
        # A larger share are submitted and denied. The rest are denied at
        # benefit verification and never start the payer PA clock.
        roll = rng.random()
        approve_share = self.cfg["approval"]["part_d_exclusion"]
        submit_share = self.cfg["exclusion_submit_share"]
        if roll < approve_share:
            submit_delay = max(
                3.0,
                hours_draw(rng, timing["submit_after_pa_required"][case["channel"]], self.sigma),
            )
            t = add_hours(t, submit_delay)
            self.push(case, "pa_required", t - timedelta(hours=2))
            self.push(case, "pa_submitted", t)
            t = add_hours(t, hours_draw(rng, timing["decision_after_ready"][case["channel"]] * speed, self.sigma))
            self.push(case, "decision_approved", t)
            return
        if roll < submit_share:
            t = add_hours(t, hours_draw(rng, 3, 0.3))
            self.push(case, "pa_required", t)
            t = add_hours(t, hours_draw(rng, timing["submit_after_pa_required"][case["channel"]], self.sigma))
            self.push(case, "pa_submitted", t)
            t = add_hours(t, hours_draw(rng, 16 * speed, self.sigma))
            self.push(case, "decision_denied", t, "plan_exclusion")
            return
        t = add_hours(t, hours_draw(rng, 2.5, 0.3))
        self.push(case, "decision_denied", t, "plan_exclusion")

    def build_cases(self) -> None:
        weights = [c["weight"] for c in CLINICS]
        span = (self.end - self.start).total_seconds()
        for index in range(self.cfg["n_cases"]):
            clinic = weighted(self.rng, CLINICS, weights)
            initiated = self.start + timedelta(seconds=self.rng.random() * span)
            drug = self.pick_drug(clinic)
            primary = self.pick_dx(drug)
            payer_id = weighted(self.rng, list(clinic["payer_mix"]), list(clinic["payer_mix"].values()))
            payer = self.payers[payer_id]
            bmi = None
            secondary = ""
            uncontrolled = 0
            attested = 0
            if drug["drug_class"] == "glp1_weight":
                bmi = round(min(62, max(24, self.rng.gauss(36.4, 5.4))), 1)
                if self.rng.random() < 0.08:
                    bmi = None
                attested = 0 if self.rng.random() < 0.07 else 1
                secondary = weighted(self.rng, [s[0] for s in SECONDARY_FOR_WEIGHT], [s[1] for s in SECONDARY_FOR_WEIGHT])
                if secondary == "I10":
                    uncontrolled = 1 if self.rng.random() < 0.55 else 0
            patient, reauth = self.choose_patient(clinic["clinic_id"], drug["drug_id"], initiated)
            birth = datetime.fromisoformat(patient["birth_date"])
            path, override = route_case(
                drug=drug,
                line_of_business=payer["line_of_business"],
                primary_dx=primary,
                initiated_at=initiated,
                bmi=bmi,
                age_years=age_on(birth, initiated),
                lifestyle_attested=attested,
                secondary_dx=secondary or None,
                uncontrolled_hypertension=uncontrolled,
            )
            if (
                path == "commercial_pa"
                and drug["drug_id"] == "drug_trulicity"
                and self.rng.random() < self.cfg["trulicity_commercial_no_pa_share"]
            ):
                path = "covered_no_pa"
            source_payer = payer_id
            if override:
                payer_id = override
                payer = self.payers[payer_id]
            channel = self.channel_for(clinic)
            expedited_share = self.cfg["expedited_share"][drug["drug_class"]]
            priority = "expedited" if self.rng.random() < expedited_share else "standard"
            match = indication_match(drug, primary, path)
            case = {
                "case_id": f"case_{index + 1:07d}",
                "clinic_id": clinic["clinic_id"],
                "patient_id": patient["patient_id"],
                "prescriber_id": self.rng.choice(
                    [p["prescriber_id"] for p in self.prescribers if p["clinic_id"] == clinic["clinic_id"]]
                ),
                "payer_id": payer_id,
                "source_payer_id": source_payer,
                "drug_id": drug["drug_id"],
                "drug_class": drug["drug_class"],
                "diagnosis_code": primary,
                "secondary_diagnosis_code": secondary,
                "channel": channel,
                "priority": priority,
                "coverage_path": path,
                "indication_match": match,
                "line_of_business": payer["line_of_business"],
                "is_reauthorization": reauth,
                "bmi": "" if bmi is None else bmi,
                "lifestyle_attested": attested,
                "uncontrolled_hypertension": uncontrolled,
                "initiated_at": initiated,
                "updated_at": initiated + timedelta(minutes=5),
                "recorded_at": initiated + timedelta(minutes=2),
                "is_duplicate_row": 0,
                "is_qa_fixture": 0,
                "diagnosis_is_dirty": 0,
                "stop_at": None,
            }
            case["stop_at"] = self.stop_plan(initiated, channel, path)
            self.cases.append(case)
            self.build_events(case, patient)
            if index and index % 10000 == 0:
                print(f"  generated {index} cases")

    def apply_mess(self) -> None:
        mess = self.cfg["mess"]
        real_events = [e for e in self.events]
        # Duplicate events. Same business time, a second raw row.
        for event in real_events:
            if self.rng.random() > mess["duplicate_event_share"]:
                continue
            copy = dict(event)
            copy["event_id"] = self.new_event_id()
            copy["recorded_at"] = event["recorded_at"] + timedelta(minutes=self.rng.uniform(4, 40))
            copy["is_duplicate_event"] = 1
            self.events.append(copy)
        # A missing business clock and a late arrival must not land on the
        # same event. Staging fills a null event_at from recorded_at, so a
        # late recorded_at on benefit_check_started would sit after the
        # approval and publish a negative time to therapy.
        late_ids = set()
        for event in real_events:
            if self.rng.random() <= mess["late_arrival_share"]:
                late_ids.add(id(event))
        for event in real_events:
            if event["event_type"] != "benefit_check_started":
                continue
            if id(event) in late_ids:
                continue
            if self.rng.random() >= mess["missing_event_at_share"]:
                continue
            event["event_at_missing"] = 1
            event["event_at"] = None
        for event in real_events:
            if id(event) not in late_ids or event["event_at"] is None:
                continue
            event["is_late_arrival"] = 1
            event["recorded_at"] = event["event_at"] + timedelta(hours=self.rng.uniform(48, 24 * 12))
        # Decision corrections. The later recorded row wins in the model.
        # Only flip a decision that already has a submission. An exclusion
        # denial at benefit check has no pa_submitted event, and turning it
        # into an approval would count as therapy with no submission.
        submitted_cases = {
            event["case_id"]
            for event in real_events
            if event["event_type"] == "pa_submitted" and event["event_at"] is not None
        }
        decisions: dict[str, list[dict]] = {}
        for event in real_events:
            if event["event_type"] in {"decision_approved", "decision_denied"}:
                decisions.setdefault(event["case_id"], []).append(event)
        for case_id, rows in decisions.items():
            if case_id not in submitted_cases:
                continue
            if len(rows) != 1 or self.rng.random() > mess["decision_correction_share"]:
                continue
            original = rows[0]
            flipped = "decision_denied" if original["event_type"] == "decision_approved" else "decision_approved"
            when = original["event_at"] + timedelta(hours=self.rng.uniform(8, 72))
            self.events.append(
                {
                    "event_id": self.new_event_id(),
                    "case_id": case_id,
                    "clinic_id": original["clinic_id"],
                    "event_type": flipped,
                    "event_at": when,
                    "recorded_at": when + timedelta(minutes=20),
                    "denial_reason_code": "not_medically_necessary" if flipped == "decision_denied" else "",
                    "denial_reason_text": "",
                    "is_correction": 1,
                    "is_duplicate_event": 0,
                    "is_late_arrival": 0,
                    "event_at_missing": 0,
                    "is_chronology_error": 0,
                    "source_channel": original["source_channel"],
                }
            )
        submitted = {}
        for event in self.events:
            if event["event_type"] == "pa_submitted" and event["event_at"] is not None:
                submitted[event["case_id"]] = event["event_at"]
        for event in real_events:
            if event["event_type"] not in {"decision_approved", "decision_denied"}:
                continue
            if event["event_at"] is None or event["case_id"] not in submitted:
                continue
            if self.rng.random() > mess["chronology_error_share"]:
                continue
            submit_at = submitted[event["case_id"]]
            event["event_at"] = submit_at - timedelta(hours=3)
            event["is_chronology_error"] = 1
            if event["recorded_at"] < submit_at:
                event["recorded_at"] = submit_at + timedelta(hours=5)
        for case in self.cases:
            if self.rng.random() > mess["dirty_diagnosis_share"]:
                continue
            code = case["diagnosis_code"].replace(".", "")
            if self.rng.random() < 0.5:
                code = code.lower()
            case["diagnosis_code"] = code
            case["diagnosis_is_dirty"] = 1
        extras = []
        for case in self.cases:
            if self.rng.random() > mess["duplicate_case_share"]:
                continue
            older = dict(case)
            older["updated_at"] = case["updated_at"] - timedelta(days=2)
            older["channel"] = {"epa": "fax", "fax": "phone", "phone": "epa"}[case["channel"]]
            older["is_duplicate_row"] = 1
            extras.append(older)
        self.cases.extend(extras)
        denial_events = [
            e
            for e in self.events
            if e["denial_reason_code"] and e["event_type"] in {"decision_denied", "appeal_denied", "info_requested"}
        ]
        for event in denial_events:
            roll = self.rng.random()
            if roll < mess["unmapped_denial_text_share"]:
                event["denial_reason_code"] = ""
                event["denial_reason_text"] = "see scanned notes"
            elif roll < mess["unmapped_denial_text_share"] + mess["mapped_free_text_share"]:
                code = event["denial_reason_code"]
                if code in FREE_TEXT_BY_CODE:
                    event["denial_reason_text"] = FREE_TEXT_BY_CODE[code]
                    event["denial_reason_code"] = ""
        # Duplicate patient rows with a shorter first name. Staging keeps the latest.
        dup_patients = []
        for patient in list(self.patients):
            if self.rng.random() > mess["duplicate_patient_share"]:
                continue
            older = dict(patient)
            older["first_name"] = patient["first_name"][:-1] or patient["first_name"]
            older["updated_at"] = patient["updated_at"] - timedelta(days=30)
            older["is_duplicate_row"] = 1
            dup_patients.append(older)
        self.patients.extend(dup_patients)

    def add_fixtures(self) -> None:
        """Hand-built cases the dbt tests assert against. Excluded from metrics."""
        clinic = "clinic_lakeshore"
        patient = self.make_patient(clinic, datetime(2026, 1, 1))
        patient["patient_id"] = "pat_qa"
        prescriber = next(p["prescriber_id"] for p in self.prescribers if p["clinic_id"] == clinic)
        base = {
            "clinic_id": clinic,
            "patient_id": "pat_qa",
            "prescriber_id": prescriber,
            "payer_id": "payer_aetna_com",
            "source_payer_id": "payer_aetna_com",
            "drug_id": "drug_ozempic",
            "drug_class": "glp1_diabetes",
            "diagnosis_code": "E11.9",
            "secondary_diagnosis_code": "",
            "channel": "epa",
            "priority": "standard",
            "coverage_path": "commercial_pa",
            "indication_match": "on_label",
            "line_of_business": "commercial",
            "is_reauthorization": 0,
            "bmi": "",
            "lifestyle_attested": 0,
            "uncontrolled_hypertension": 0,
            "is_duplicate_row": 0,
            "is_qa_fixture": 1,
            "diagnosis_is_dirty": 0,
            "stop_at": None,
        }

        def add_case(case_id: str, initiated: datetime, **overrides) -> dict:
            case = dict(base)
            case.update(overrides)
            case["case_id"] = case_id
            case["initiated_at"] = initiated
            case["updated_at"] = initiated
            case["recorded_at"] = initiated
            self.cases.append(case)
            return case

        def add_event(case, event_type, when, recorded, **overrides):
            row = {
                "event_id": self.new_event_id(),
                "case_id": case["case_id"],
                "clinic_id": case["clinic_id"],
                "event_type": event_type,
                "event_at": when,
                "recorded_at": recorded,
                "denial_reason_code": "",
                "denial_reason_text": "",
                "is_correction": 0,
                "is_duplicate_event": 0,
                "is_late_arrival": 0,
                "event_at_missing": 0,
                "is_chronology_error": 0,
                "source_channel": case["channel"],
            }
            row.update(overrides)
            self.events.append(row)

        started = datetime(2026, 3, 1, 9, 0, 0)
        case = add_case("qa_correction", started)
        add_event(case, "benefit_check_started", started, started + timedelta(minutes=5))
        add_event(case, "benefit_verified", started + timedelta(hours=2), started + timedelta(hours=2, minutes=5))
        add_event(case, "pa_required", started + timedelta(hours=3), started + timedelta(hours=3, minutes=5))
        submitted = started + timedelta(hours=6)
        add_event(case, "pa_submitted", submitted, submitted + timedelta(minutes=10))
        approved = datetime(2026, 3, 2, 12, 0, 0)
        add_event(case, "decision_approved", approved, approved + timedelta(minutes=15))
        denied = datetime(2026, 3, 3, 9, 0, 0)
        add_event(
            case,
            "decision_denied",
            denied,
            denied + timedelta(minutes=20),
            denial_reason_code="not_medically_necessary",
            is_correction=1,
        )

        started = datetime(2026, 2, 1, 9, 0, 0)
        case = add_case("qa_late", started, channel="fax")
        add_event(
            case,
            "benefit_check_started",
            started,
            datetime(2026, 2, 8, 9, 0, 0),
            is_late_arrival=1,
        )
        submitted = datetime(2026, 2, 1, 12, 0, 0)
        add_event(case, "pa_required", datetime(2026, 2, 1, 11, 0, 0), datetime(2026, 2, 9, 11, 0, 0), is_late_arrival=1)
        add_event(case, "pa_submitted", submitted, datetime(2026, 2, 10, 12, 0, 0), is_late_arrival=1)
        decided = datetime(2026, 2, 2, 0, 0, 0)
        add_event(case, "decision_approved", decided, datetime(2026, 2, 12, 0, 0, 0), is_late_arrival=1)
        add_event(case, "benefit_verified", datetime(2026, 2, 1, 10, 0, 0), datetime(2026, 2, 8, 12, 0, 0), is_late_arrival=1)

        started = datetime(2026, 1, 15, 8, 0, 0)
        case = add_case("qa_chrono", started)
        add_event(case, "benefit_check_started", started, started + timedelta(minutes=5))
        add_event(case, "benefit_verified", datetime(2026, 1, 15, 9, 0, 0), datetime(2026, 1, 15, 9, 10, 0))
        add_event(case, "pa_required", datetime(2026, 1, 15, 9, 30, 0), datetime(2026, 1, 15, 9, 40, 0))
        submitted = datetime(2026, 1, 15, 12, 0, 0)
        add_event(case, "pa_submitted", submitted, submitted + timedelta(minutes=10))
        add_event(
            case,
            "decision_approved",
            submitted - timedelta(hours=3),
            datetime(2026, 1, 16, 15, 0, 0),
            is_chronology_error=1,
        )

        started = datetime(2026, 4, 1, 10, 0, 0)
        case = add_case("qa_dup_case", started, channel="fax")
        older = dict(case)
        older["channel"] = "phone"
        older["updated_at"] = started - timedelta(days=3)
        older["is_duplicate_row"] = 1
        self.cases.append(older)
        add_event(case, "benefit_check_started", started, started + timedelta(minutes=5))
        add_event(case, "benefit_verified", started + timedelta(hours=4), started + timedelta(hours=4, minutes=5))
        add_event(case, "pa_not_required", started + timedelta(hours=5), started + timedelta(hours=5, minutes=5))
        case["coverage_path"] = "covered_no_pa"

        started = datetime(2026, 5, 1, 10, 0, 0)
        case = add_case("qa_dirty_dx", started, diagnosis_code="E119", diagnosis_is_dirty=1)
        add_event(case, "benefit_check_started", started, started + timedelta(minutes=4))
        add_event(case, "benefit_verified", started + timedelta(hours=1), started + timedelta(hours=1, minutes=4))
        add_event(case, "pa_required", started + timedelta(hours=2), started + timedelta(hours=2, minutes=4))
        add_event(case, "pa_submitted", started + timedelta(hours=5), started + timedelta(hours=5, minutes=4))
        add_event(case, "decision_approved", started + timedelta(hours=30), started + timedelta(hours=30, minutes=4))

        started = datetime(2026, 6, 1, 10, 0, 0)
        case = add_case("qa_dup_event", started)
        add_event(case, "benefit_check_started", started, started + timedelta(minutes=5))
        add_event(
            case,
            "benefit_check_started",
            started,
            started + timedelta(minutes=25),
            is_duplicate_event=1,
        )
        add_event(case, "benefit_verified", started + timedelta(hours=2), started + timedelta(hours=2, minutes=5))
        add_event(case, "pa_not_required", started + timedelta(hours=3), started + timedelta(hours=3, minutes=5))
        case["coverage_path"] = "covered_no_pa"

    def load(self, db_path: Path) -> None:
        RAW.mkdir(parents=True, exist_ok=True)
        case_fields = [
            "case_id", "clinic_id", "patient_id", "prescriber_id", "payer_id", "source_payer_id",
            "drug_id", "diagnosis_code", "secondary_diagnosis_code", "channel", "priority",
            "coverage_path", "indication_match", "is_reauthorization", "bmi", "lifestyle_attested",
            "uncontrolled_hypertension", "initiated_at", "updated_at", "recorded_at",
            "is_duplicate_row", "is_qa_fixture", "diagnosis_is_dirty",
        ]
        event_fields = [
            "event_id", "case_id", "clinic_id", "event_type", "event_at", "recorded_at",
            "denial_reason_code", "denial_reason_text", "is_correction", "is_duplicate_event",
            "is_late_arrival", "event_at_missing", "is_chronology_error", "source_channel",
        ]
        patient_fields = [
            "patient_id", "clinic_id", "first_name", "last_name", "birth_date", "member_id",
            "updated_at", "is_duplicate_row",
        ]
        write_csv(RAW / "cases.csv", self.cases, case_fields)
        write_csv(RAW / "case_events.csv", self.events, event_fields)
        write_csv(RAW / "patients.csv", self.patients, patient_fields)
        write_csv(
            RAW / "prescribers.csv",
            self.prescribers,
            ["prescriber_id", "clinic_id", "npi", "first_name", "last_name", "specialty", "npi_is_synthetic"],
        )
        write_csv(
            RAW / "clinics.csv",
            CLINICS,
            ["clinic_id", "clinic_name", "city", "state", "organization_type", "is_featured"],
        )
        write_csv(
            RAW / "payers.csv",
            PAYERS,
            ["payer_id", "payer_name", "line_of_business", "is_fictional_plan"],
        )
        write_csv(
            RAW / "drugs.csv",
            DRUGS,
            [
                "drug_id", "brand_name", "generic_name", "rxcui", "product_ndc", "package_ndc",
                "labeler_name", "dosage_form", "strength_description", "drug_class", "bridge_eligible",
            ],
        )
        dx_rows = [{"diagnosis_code": k, "description": v} for k, v in DIAGNOSES.items()]
        write_csv(RAW / "diagnoses.csv", dx_rows, ["diagnosis_code", "description"])
        meta = {
            "seed": self.cfg["seed"],
            "as_of": self.cfg["as_of"],
            "window_start": self.cfg["window_start"],
            "window_end": self.cfg["window_end"],
            "bridge_start": self.cfg["bridge_start"],
            "n_case_rows": len(self.cases),
            "n_event_rows": len(self.events),
            "n_patient_rows": len(self.patients),
            "featured_clinic_id": FEATURED_CLINIC_ID,
        }
        write_csv(RAW / "generation_meta.csv", [{"meta_key": k, "meta_value": v} for k, v in meta.items()], ["meta_key", "meta_value"])

        if db_path.exists():
            db_path.unlink()
        con = duckdb.connect(str(db_path))
        con.execute("create schema if not exists raw")
        loads = {
            "clinics": "clinics.csv",
            "payers": "payers.csv",
            "drugs": "drugs.csv",
            "prescribers": "prescribers.csv",
            "patients": "patients.csv",
            "cases": "cases.csv",
            "case_events": "case_events.csv",
            "diagnoses": "diagnoses.csv",
            "generation_meta": "generation_meta.csv",
        }
        for table, filename in loads.items():
            con.execute(
                f"""
                create or replace table raw.{table} as
                select * from read_csv(?, header=true, all_varchar=true)
                """,
                [str(RAW / filename)],
            )
        con.close()
        distinct_cases = len({c["case_id"] for c in self.cases})
        print(f"cases_distinct={distinct_cases} case_rows={len(self.cases)} events={len(self.events)}")
        print(f"patients={len(self.patients)} prescribers={len(self.prescribers)} db={db_path}")


def main() -> None:
    cfg = load_behavior()
    db_path = Path(os.environ.get("PA_DB_PATH", ROOT / "data" / "pa.duckdb"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    builder = Builder(cfg)
    print("generating dimensions and cases")
    builder.make_dimensions()
    builder.build_cases()
    print("applying mess and fixtures")
    builder.apply_mess()
    builder.add_fixtures()
    builder.load(db_path)


if __name__ == "__main__":
    main()
