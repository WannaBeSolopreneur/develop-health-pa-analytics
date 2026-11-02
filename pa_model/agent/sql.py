"""SQL for catalog metrics.

The select list is fixed here. The parser never contributes a column, a
function, or a fragment of the question text.
"""

from __future__ import annotations

import re

from pa_model.agent.parse import Plan

# Expressions are Snowflake-compatible. median, nullif, and count(column)
# behave the same in DuckDB and Snowflake for this use.
METRIC_SQL: dict[str, dict] = {
    "approval_rate": {
        "select": [
            "sum(is_final_approved) as approval_numerator",
            "sum(is_decision_denominator) as approval_denominator",
            "sum(is_final_approved) * 1.0 / nullif(sum(is_decision_denominator), 0) as approval_rate",
        ],
        "value": "approval_rate",
        "numerator": "approval_numerator",
        "denominator": "approval_denominator",
    },
    "first_pass_approval_rate": {
        "select": [
            "sum(is_first_pass_approved) as first_pass_numerator",
            "sum(is_first_pass_denominator) as first_pass_denominator",
            "sum(is_first_pass_approved) * 1.0 / nullif(sum(is_first_pass_denominator), 0) as first_pass_approval_rate",
        ],
        "value": "first_pass_approval_rate",
        "numerator": "first_pass_numerator",
        "denominator": "first_pass_denominator",
    },
    "median_hours_to_decision": {
        "select": [
            "median(hours_to_decision) as median_hours_to_decision",
            "count(hours_to_decision) as decision_duration_n",
        ],
        "value": "median_hours_to_decision",
        "n": "decision_duration_n",
    },
    "abandonment_rate": {
        "select": [
            "sum(is_abandoned) as abandon_numerator",
            "sum(is_abandon_denominator) as abandon_denominator",
            "sum(is_abandoned) * 1.0 / nullif(sum(is_abandon_denominator), 0) as abandonment_rate",
        ],
        "value": "abandonment_rate",
        "numerator": "abandon_numerator",
        "denominator": "abandon_denominator",
    },
    "appeal_overturn_rate": {
        "select": [
            "sum(is_appeal_overturned) as appeal_numerator",
            "sum(is_appeal_denominator) as appeal_denominator",
            "sum(is_appeal_overturned) * 1.0 / nullif(sum(is_appeal_denominator), 0) as appeal_overturn_rate",
        ],
        "value": "appeal_overturn_rate",
        "numerator": "appeal_numerator",
        "denominator": "appeal_denominator",
    },
    "time_to_therapy": {
        "select": [
            "median(hours_to_therapy) as median_hours_to_therapy",
            "count(hours_to_therapy) as therapy_duration_n",
        ],
        "value": "median_hours_to_therapy",
        "n": "therapy_duration_n",
    },
    "cases_stuck": {
        "select": ["sum(is_stuck) as cases_stuck"],
        "where": ["is_stuck = 1"],
        "value": "cases_stuck",
    },
    "case_count": {
        "select": ["count(*) as case_count"],
        "value": "case_count",
    },
}

ALLOWED_COLUMNS = {
    "payer_name",
    "drug_name",
    "channel",
    "line_of_business",
    "drug_class",
    "priority",
    "coverage_path",
    "case_month",
    "stuck_stage",
    "clinic_id",
}

_VALUE = re.compile(r"[A-Za-z0-9_ .'()-]+")


def quote_literal(value: str) -> str:
    if not isinstance(value, str) or not _VALUE.fullmatch(value):
        raise ValueError(f"refusing to place this value in SQL: {value!r}")
    if "'" in value or ";" in value:
        raise ValueError(f"refusing to place this value in SQL: {value!r}")
    return "'" + value + "'"


def render_sql(plan: Plan) -> str:
    spec = METRIC_SQL[plan.metric_id]
    group = plan.group_by
    if group is not None and group not in ALLOWED_COLUMNS:
        raise ValueError(group)
    select_bits = []
    if group:
        select_bits.append(group)
    select_bits.extend(spec["select"])
    where = ["is_qa_fixture = 0"]
    if plan.clinic_id:
        where.append(f"clinic_id = {quote_literal(plan.clinic_id)}")
    for key, value in plan.filters:
        if key not in ALLOWED_COLUMNS or key == "clinic_id":
            raise ValueError(key)
        where.append(f"{key} = {quote_literal(value)}")
    where.extend(spec.get("where", []))
    lines = [
        "select",
        "    " + ",\n    ".join(select_bits),
        "from marts.fct_pa_cases",
        "where " + "\n  and ".join(where),
    ]
    if group:
        lines.append(f"group by {group}")
        lines.append(f"order by {group}")
    return "\n".join(lines)
