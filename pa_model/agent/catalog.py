"""Load the semantic allow list and the dimension values the parser may use."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pa_model.reference import CLINICS, DRUGS, PAYERS

ROOT = Path(__file__).resolve().parents[2]
METRICS_PATH = ROOT / "semantic" / "metrics.yml"

# Aliases that would silently pick one plan or one product.
SKIP_PAYER_ALIASES = {"blue cross", "bcbs", "bridge", "medicaid"}


def normalize(text: str) -> str:
    lowered = text.lower().replace("’", "'")
    for dash in ("-", "–", "—", "/"):
        lowered = lowered.replace(dash, " ")
    cleaned = []
    for char in lowered:
        cleaned.append(char if char.isalnum() or char.isspace() else " ")
    collapsed = " ".join("".join(cleaned).split())
    # Whole tokens only. A raw replace would turn "coverage path" into "coveragepath".
    padded = f" {collapsed} "
    padded = padded.replace(" glp 1 ", " glp1 ")
    padded = padded.replace(" e pa ", " epa ")
    return padded.strip()


@dataclass
class Metric:
    metric_id: str
    name: str
    format: str
    aliases: list[str]
    group_by: list[str]
    definition: str


@dataclass
class DimValue:
    dimension: str
    value: str
    label: str
    aliases: list[str]


@dataclass
class Catalog:
    metrics: dict[str, Metric]
    values: list[DimValue]
    ambiguous: list[dict]
    clinics: list[dict]
    featured_clinic_id: str
    featured_clinic_name: str
    group_aliases: dict[str, str]
    blocked: list[dict]
    stopwords: set[str]
    stage_labels: dict[str, str]
    value_labels: dict[tuple[str, str], str] = field(default_factory=dict)

    def metric_alias_pairs(self) -> list[tuple[str, str]]:
        pairs = [(alias, metric.metric_id) for metric in self.metrics.values() for alias in metric.aliases]
        pairs.sort(key=lambda item: len(item[0]), reverse=True)
        return pairs


def _dedupe(aliases: list[str]) -> list[str]:
    seen = []
    for alias in aliases:
        normalized = normalize(alias)
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


def load_catalog(path: Path = METRICS_PATH) -> Catalog:
    from pa_model.agent.sql import METRIC_SQL

    raw = yaml.safe_load(path.read_text())
    metrics: dict[str, Metric] = {}
    for metric_id, body in raw["metrics"].items():
        if metric_id not in METRIC_SQL:
            raise SystemExit(f"{metric_id} is in the semantic file and has no SQL expression")
        definition = " ".join(body["definition"].split())
        metrics[metric_id] = Metric(
            metric_id=metric_id,
            name=body["name"],
            format=body["format"],
            aliases=_dedupe(body["aliases"]),
            group_by=list(body["group_by"]),
            definition=definition,
        )
    missing = set(METRIC_SQL) - set(metrics)
    if missing:
        raise SystemExit(f"SQL exists for metrics that are not in the allow list: {sorted(missing)}")

    values: list[DimValue] = []

    def add(dimension: str, value: str, label: str, aliases: list[str]) -> None:
        values.append(DimValue(dimension, value, label, _dedupe(aliases)))

    extra_payer_aliases = {
        "payer_uhc_com": ["united healthcare commercial", "united healthcare commercial ppo"],
        "payer_uhc_ma": ["united healthcare medicare", "united healthcare medicare advantage"],
    }
    for payer in PAYERS:
        aliases = [alias for alias in payer["aliases"] if normalize(alias) not in SKIP_PAYER_ALIASES]
        aliases.extend(extra_payer_aliases.get(payer["payer_id"], []))
        aliases.append(payer["payer_name"])
        add("payer_name", payer["payer_name"], payer["payer_name"], aliases)
    for drug in DRUGS:
        add("drug_name", drug["brand_name"], drug["brand_name"], list(drug["aliases"]) + [drug["brand_name"]])
    add("channel", "epa", "ePA", ["epa", "electronic prior authorization"])
    add("channel", "fax", "fax", ["fax"])
    add("channel", "phone", "phone", ["phone"])
    add("line_of_business", "commercial", "Commercial", ["commercial"])
    add("line_of_business", "medicare_advantage", "Medicare Advantage", ["medicare advantage"])
    add("line_of_business", "part_d", "Medicare Part D", ["part d", "medicare part d"])
    add("line_of_business", "medicaid", "Medicaid", ["medicaid"])
    add("line_of_business", "glp1_bridge", "GLP-1 Bridge line", ["bridge line", "glp1 bridge line"])
    add("drug_class", "glp1_diabetes", "GLP-1 for diabetes", ["glp1 diabetes", "glp1 for diabetes", "diabetes glp1"])
    add("drug_class", "glp1_weight", "GLP-1 for weight", ["glp1 weight", "glp1 for weight", "weight loss"])
    add("drug_class", "biologic", "Biologic", ["biologic", "biologics"])
    add("priority", "expedited", "Expedited", ["expedited", "urgent"])
    add("priority", "standard", "Standard", ["standard priority"])
    add("coverage_path", "commercial_pa", "Commercial prior authorization", ["commercial pa", "commercial prior authorization"])
    add("coverage_path", "part_d_pa", "Part D prior authorization", ["part d pa", "part d prior authorization"])
    add("coverage_path", "medicaid_pa", "Medicaid prior authorization", ["medicaid pa", "medicaid prior authorization"])
    add(
        "coverage_path",
        "part_d_exclusion",
        "Part D exclusion",
        ["part d exclusion", "plan exclusion", "exclusion"],
    )
    add("coverage_path", "glp1_bridge", "GLP-1 Bridge path", ["bridge path", "glp1 bridge path"])
    add(
        "coverage_path",
        "covered_no_pa",
        "Covered with no prior authorization",
        ["covered no pa", "no prior authorization", "covered with no prior authorization"],
    )
    months = [
        ("2025-11", "November 2025"),
        ("2025-12", "December 2025"),
        ("2026-01", "January 2026"),
        ("2026-02", "February 2026"),
        ("2026-03", "March 2026"),
        ("2026-04", "April 2026"),
        ("2026-05", "May 2026"),
        ("2026-06", "June 2026"),
        ("2026-07", "July 2026"),
        ("2026-08", "August 2026"),
        ("2026-09", "September 2026"),
        ("2026-10", "October 2026"),
        ("2026-11", "November 2026"),
    ]
    for key, label in months:
        add("case_month", key, label, [label])
    stages = [
        ("benefit_check", "Benefit check", ["benefit check"]),
        ("pa_not_submitted", "Not submitted", ["not submitted", "pa not submitted"]),
        ("awaiting_info", "Waiting on information", ["waiting on information", "awaiting info", "info requested"]),
        ("awaiting_decision", "Waiting on a decision", ["waiting on a decision", "awaiting decision"]),
        ("awaiting_appeal", "Waiting on an appeal", ["waiting on an appeal", "awaiting appeal"]),
    ]
    for key, label, aliases in stages:
        add("stuck_stage", key, label, aliases)

    clinics = []
    featured_name = ""
    for clinic in CLINICS:
        aliases = _dedupe([clinic["clinic_name"], clinic["clinic_name"].replace(" Clinic", ""), clinic["clinic_id"].replace("_", " ")])
        # A short distinctive token so "at Harbor" resolves.
        short = clinic["clinic_name"].split()[0]
        aliases = _dedupe(aliases + [short])
        clinics.append(
            {
                "clinic_id": clinic["clinic_id"],
                "name": clinic["clinic_name"],
                "aliases": aliases,
                "featured": clinic["clinic_id"] == raw["featured_clinic_id"],
            }
        )
        if clinic["clinic_id"] == raw["featured_clinic_id"]:
            featured_name = clinic["clinic_name"]

    ambiguous = [
        {
            "phrase": "zepbound",
            "choices": ["Zepbound KwikPen", "Zepbound single-dose pen"],
            "noun": "drug",
        },
        {
            "phrase": "unitedhealthcare",
            "choices": ["UnitedHealthcare Commercial PPO", "UnitedHealthcare Medicare Advantage"],
            "noun": "plan",
        },
        {
            "phrase": "united healthcare",
            "choices": ["UnitedHealthcare Commercial PPO", "UnitedHealthcare Medicare Advantage"],
            "noun": "plan",
        },
        {
            "phrase": "uhc",
            "choices": ["UnitedHealthcare Commercial PPO", "UnitedHealthcare Medicare Advantage"],
            "noun": "plan",
        },
        {
            "phrase": "semaglutide",
            "choices": ["Ozempic", "Wegovy"],
            "noun": "drug",
        },
        {
            "phrase": "tirzepatide",
            "choices": ["Mounjaro", "Zepbound KwikPen", "Zepbound single-dose pen"],
            "noun": "drug",
        },
    ]

    group_aliases = {
        "payer": "payer_name",
        "payers": "payer_name",
        "plan": "payer_name",
        "plans": "payer_name",
        "drug": "drug_name",
        "drugs": "drug_name",
        "channel": "channel",
        "channels": "channel",
        "line of business": "line_of_business",
        "drug class": "drug_class",
        "class": "drug_class",
        "priority": "priority",
        "coverage path": "coverage_path",
        "path": "coverage_path",
        "month": "case_month",
        "months": "case_month",
        "stage": "stuck_stage",
        "stages": "stuck_stage",
    }
    blocked = [
        {
            "phrases": ["revenue", "reimbursement", "dollar", "profit"],
            "message": "Revenue is not a defined metric. I will not estimate it.",
        },
        {
            "phrases": ["satisfaction", "nps", "experience score"],
            "message": "Patient satisfaction is not a defined metric. I will not estimate it.",
        },
        {
            "phrases": ["forecast", "predict", "projection", "next quarter", "next month"],
            "message": "A forecast is not a defined metric. I will not estimate it.",
        },
        {
            "phrases": ["denial reason", "why denied", "denied because", "why was it denied"],
            "message": "Denial reason is not a defined metric. I will not summarize reasons that were not asked as a metric.",
        },
        {
            "phrases": ["patient name", "date of birth", "npi", "patient id", "phi"],
            "message": "Patient level detail is not available to this agent. I will not return it.",
        },
    ]
    stopwords = {
        "a",
        "across",
        "all",
        "an",
        "and",
        "are",
        "at",
        "breakdown",
        "by",
        "clinics",
        "case",
        "cases",
        "clinic",
        "do",
        "does",
        "every",
        "for",
        "get",
        "how",
        "in",
        "is",
        "it",
        "long",
        "many",
        "me",
        "median",
        "my",
        "number",
        "of",
        "on",
        "only",
        "other",
        "our",
        "patient",
        "patients",
        "please",
        "show",
        "stall",
        "take",
        "tell",
        "tenants",
        "that",
        "the",
        "there",
        "this",
        "time",
        "to",
        "what",
        "where",
        "with",
        "hours",
        "hour",
        "rate",
        "percent",
        "percentage",
        "among",
    }
    stage_labels = {key: label for key, label, _aliases in stages}
    catalog = Catalog(
        metrics=metrics,
        values=values,
        ambiguous=ambiguous,
        clinics=clinics,
        featured_clinic_id=raw["featured_clinic_id"],
        featured_clinic_name=featured_name,
        group_aliases=group_aliases,
        blocked=blocked,
        stopwords=stopwords,
        stage_labels=stage_labels,
    )
    for item in values:
        catalog.value_labels[(item.dimension, item.value)] = item.label
    return catalog
