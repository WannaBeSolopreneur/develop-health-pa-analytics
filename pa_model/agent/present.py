"""Turn SQL rows into sentences. Formatting only. No new arithmetic on rates."""

from __future__ import annotations

from pa_model.agent.catalog import Catalog
from pa_model.agent.parse import Plan
from pa_model.agent.sql import METRIC_SQL

GROUP_WORD = {
    "payer_name": "payer",
    "drug_name": "drug",
    "channel": "channel",
    "line_of_business": "line of business",
    "drug_class": "drug class",
    "priority": "priority",
    "coverage_path": "coverage path",
    "case_month": "month",
    "stuck_stage": "stage",
}


def assert_plain(text: str, label: str) -> None:
    for bad in (";", "—", "–"):
        if bad in text:
            raise SystemExit(f"{label} contains {bad!r}: {text[:240]}")


def commas(value: int) -> str:
    return f"{value:,}"


def as_int(value) -> int | None:
    if value is None:
        return None
    return int(value)


def as_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def hours_label(value: float) -> str:
    return f"{value:.1f} hours"


def slice_phrase(plan: Plan, catalog: Catalog) -> str:
    if not plan.filters:
        return ""
    labels = [catalog.value_labels[(dimension, value)] for dimension, value in plan.filters]
    if len(labels) == 1:
        return f" filtered to {labels[0]}"
    return " filtered to " + " and ".join(labels)


def _unavailable(metric_name: str, why: str) -> str:
    return f"There are no {why} in this slice, so the {metric_name.lower()} is not available."


def scalar_message(plan: Plan, row: dict, catalog: Catalog) -> str:
    metric = catalog.metrics[plan.metric_id]
    spec = METRIC_SQL[plan.metric_id]
    scope = slice_phrase(plan, catalog)
    if metric.format == "rate":
        numerator = as_int(row[spec["numerator"]])
        denominator = as_int(row[spec["denominator"]])
        rate = as_float(row[spec["value"]])
        if not denominator or rate is None:
            if plan.metric_id == "abandonment_rate":
                return _unavailable(metric.name, "closed cases that reached prior authorization")
            if plan.metric_id == "appeal_overturn_rate":
                return _unavailable(metric.name, "appeal decisions")
            return _unavailable(metric.name, "decided cases")
        if plan.metric_id == "approval_rate":
            tail = "decided cases were approved"
        elif plan.metric_id == "first_pass_approval_rate":
            tail = "decided cases were approved on the first pass, with no information request and no appeal"
        elif plan.metric_id == "abandonment_rate":
            tail = "cases that reached prior authorization and then closed were abandoned"
        else:
            tail = "appeal decisions were approvals"
        return (
            f"The {metric.name.lower()}{scope} is {pct(rate)}. "
            f"{commas(numerator)} of {commas(denominator)} {tail}."
        )
    if metric.format == "hours":
        value = as_float(row[spec["value"]])
        n = as_int(row[spec["n"]])
        if plan.metric_id == "time_to_therapy":
            if not n or value is None:
                return _unavailable(metric.name, "approved cases with both clocks")
            return (
                f"The time to therapy{scope} is {hours_label(value)}. "
                f"The median uses {commas(n)} approved cases. "
                "This clock ends at approval. A pharmacy fill is outside this metric."
            )
        if not n or value is None:
            return _unavailable(metric.name, "cases with both a submission and a decision")
        return (
            f"The median hours to decision{scope} is {hours_label(value)}. "
            f"The median uses {commas(n)} cases with both a submission and a decision."
        )
    count = as_int(row[spec["value"]]) or 0
    if plan.metric_id == "cases_stuck":
        noun = "case is" if count == 1 else "cases are"
        return (
            f"{commas(count)} {noun} stuck{scope}. "
            "A case is stuck when it is still open and has been in the current stage longer than the SLA."
        )
    noun = "case" if count == 1 else "cases"
    return f"There are {commas(count)} {noun}{scope}."


def grouped_message(plan: Plan, catalog: Catalog) -> str:
    metric = catalog.metrics[plan.metric_id]
    word = GROUP_WORD[plan.group_by]
    return f"{metric.name} by {word}{slice_phrase(plan, catalog)}."


def grouped_table(plan: Plan, rows: list[dict], catalog: Catalog) -> dict:
    metric = catalog.metrics[plan.metric_id]
    spec = METRIC_SQL[plan.metric_id]
    group = plan.group_by
    assert group is not None
    if metric.format == "rate":
        headers = [GROUP_WORD[group].capitalize(), metric.name, "Cases"]
    elif metric.format == "hours":
        headers = [GROUP_WORD[group].capitalize(), "Median", "Cases in the median"]
    else:
        headers = [GROUP_WORD[group].capitalize(), metric.name]
    body = []
    ordered = sorted(rows, key=lambda row: str(row.get(group) or ""))
    if group == "channel":
        order = {"epa": 0, "fax": 1, "phone": 2}
        ordered.sort(key=lambda row: order.get(row.get(group), 9))
    if group == "stuck_stage":
        order = {
            "benefit_check": 0,
            "pa_not_submitted": 1,
            "awaiting_info": 2,
            "awaiting_decision": 3,
            "awaiting_appeal": 4,
        }
        ordered.sort(key=lambda row: order.get(row.get(group), 9))
    for row in ordered:
        raw = row.get(group)
        label = catalog.value_labels.get((group, raw), str(raw))
        if metric.format == "rate":
            numerator = as_int(row[spec["numerator"]]) or 0
            denominator = as_int(row[spec["denominator"]]) or 0
            rate = as_float(row[spec["value"]])
            rate_text = "not available" if rate is None or not denominator else pct(rate)
            body.append([label, rate_text, f"{commas(numerator)} of {commas(denominator)}"])
        elif metric.format == "hours":
            value = as_float(row[spec["value"]])
            n = as_int(row[spec["n"]]) or 0
            value_text = "not available" if value is None or not n else hours_label(value)
            body.append([label, value_text, commas(n)])
        else:
            body.append([label, commas(as_int(row[spec["value"]]) or 0)])
    return {"headers": headers, "rows": body}
