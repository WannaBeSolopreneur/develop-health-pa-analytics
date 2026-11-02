"""Write the static clinic page data from the warehouse and the ask agent."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from pa_model.agent.ask import ask, run_sql
from pa_model.agent.catalog import ROOT, load_catalog
from pa_model.agent.parse import Plan, parse_question, parser_lexicon
from pa_model.agent.present import as_float, as_int, assert_plain, commas, hours_label, pct

SITE = ROOT / "site"
MISSING = (
    "That question maps to a defined metric, but this page only has a precomputed "
    "answer for the clinic overall, one breakdown, or one filter. I will not estimate the missing slice."
)

FIXTURES = [
    "What is the approval rate?",
    "median hours to decision by channel",
    "approval rate for Wegovy",
    "approval rate for zepbound",
    "approval rate for unitedhealthcare",
    "approval rate for united healthcare",
    "approval rate for UnitedHealthcare Commercial PPO",
    "what is our revenue this quarter",
    "approval rate at Harbor Endocrine",
    "how many cases are stuck",
    "where are cases stuck",
    "cases stuck by stage",
    "first pass approval rate",
    "time to therapy for Ozempic",
    "abandonment rate for fax",
    "appeal overturn rate",
    "how many cases",
    "approval rate by payer for Wegovy",
    "approval rate by payer for Aetna",
    "approval rate for aspirin",
    "what is the average bmi",
    "patient satisfaction with fax",
    "approval rate for Zepbound KwikPen",
    "semaglutide approval rate",
    "forecast the approval rate",
    "denial reason for wegovy",
    "approval rate across all clinics",
    "approval rate for Wegovy on fax",
    "median hours to decision for fax",
    "how many cases are stuck by stage",
    "approval rate for medicaid",
    "time to therapy",
]


def _plans(catalog) -> list[Plan]:
    clinic = catalog.featured_clinic_id
    plans = []
    values_by_dim: dict[str, list] = {}
    for item in catalog.values:
        values_by_dim.setdefault(item.dimension, []).append(item)
    for metric in catalog.metrics.values():
        plans.append(Plan(metric.metric_id, None, [], clinic, "clinic"))
        for dimension in metric.group_by:
            plans.append(Plan(metric.metric_id, dimension, [], clinic, "clinic"))
            for item in values_by_dim.get(dimension, []):
                plans.append(Plan(metric.metric_id, None, [(dimension, item.value)], clinic, "clinic"))
    return plans


def _merge_breakdown(rate_table: dict, hours_table: dict) -> list[dict]:
    hours = {row[0]: row for row in hours_table["rows"]}
    merged = []
    for row in rate_table["rows"]:
        hour_row = hours.get(row[0])
        merged.append(
            {
                "name": row[0],
                "rate": row[1],
                "decided": row[2],
                "hours": hour_row[1] if hour_row else "not available",
                "hour_n": hour_row[2] if hour_row else "0",
            }
        )
    return merged


def _funnel_block(row: dict) -> dict:
    stages = [
        ("Benefit check started", int(row["cases_started"])),
        ("Benefit verified", int(row["cases_benefit_verified"])),
        ("Prior authorization required", int(row["cases_pa_required"])),
        ("Submitted", int(row["cases_submitted"])),
        ("Decided after submission", int(row["cases_decided_after_submit"])),
        ("Approved", int(row["cases_approved"])),
    ]
    started = stages[0][1] or 1
    funnel = [
        {"label": label, "count_label": commas(count), "width_pct": round(100 * count / started, 1)}
        for label, count in stages
    ]
    exits = [
        {"label": "Covered with no prior authorization", "count_label": commas(int(row["cases_covered_no_pa"]))},
        {"label": "Denied before submission", "count_label": commas(int(row["cases_denied_before_submission"]))},
        {"label": "Abandoned", "count_label": commas(int(row["cases_abandoned"]))},
    ]
    stuck = [
        {"label": "Benefit check", "count_label": commas(int(row["stuck_benefit_check"]))},
        {"label": "Not submitted", "count_label": commas(int(row["stuck_pa_not_submitted"]))},
        {"label": "Waiting on information", "count_label": commas(int(row["stuck_awaiting_info"]))},
        {"label": "Waiting on a decision", "count_label": commas(int(row["stuck_awaiting_decision"]))},
        {"label": "Waiting on an appeal", "count_label": commas(int(row["stuck_awaiting_appeal"]))},
    ]
    return {"funnel": funnel, "exits": exits, "stuck": stuck}


def _date_label(value: str) -> str:
    months = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }
    day = value[:10]
    year, month, dd = day.split("-")
    return f"{months[month]} {int(dd)}, {year}"


def main() -> None:
    catalog = load_catalog()
    behavior = yaml.safe_load((ROOT / "assumptions" / "behavior.yml").read_text())
    connection = __import__("duckdb").connect(
        __import__("os").environ.get("PA_DB_PATH", str(ROOT / "data" / "pa.duckdb")),
        read_only=True,
    )
    try:
        mart = run_sql(
            "select * from marts.mart_headline_metrics where clinic_id = 'clinic_lakeshore'",
            connection,
        )[0]
        funnel_row = run_sql(
            "select * from marts.mart_pa_funnel where clinic_id = 'clinic_lakeshore'",
            connection,
        )[0]
        checks = {
            "What is the approval rate?": ("approval_rate", "approval_rate"),
            "first pass approval rate": ("first_pass_approval_rate", "first_pass_approval_rate"),
            "median hours to decision": ("median_hours_to_decision", "median_hours_to_decision"),
            "abandonment rate": ("abandonment_rate", "abandonment_rate"),
            "appeal overturn rate": ("appeal_overturn_rate", "appeal_overturn_rate"),
            "time to therapy": ("median_hours_to_therapy", "median_hours_to_therapy"),
            "how many cases are stuck": ("cases_stuck", "cases_stuck"),
            "how many cases": ("case_count", "cases"),
        }
        for question, (agent_col, mart_col) in checks.items():
            answer = ask(question, use_llm=False, connection=connection, catalog=catalog)
            if answer.status != "ok":
                raise SystemExit(f"export check failed: {question}")
            left = answer.rows[0][agent_col]
            right = mart[mart_col]
            if abs(float(left) - float(right)) > 1e-9:
                raise SystemExit(f"{question} does not match the headline mart: {left} vs {right}")
        if int(funnel_row["cases_started"]) != int(mart["cases"]):
            raise SystemExit("funnel start count does not match the headline case count")

        answers = {}
        plans = _plans(catalog)
        for index, plan in enumerate(plans, start=1):
            signature = plan.signature()
            if signature in answers:
                raise SystemExit(f"duplicate signature {signature}")
            # Reuse ask() so the page text is the agent text. The plan is already valid.
            sql_answer = ask(
                _question_for(plan, catalog),
                use_llm=False,
                connection=connection,
                catalog=catalog,
            )
            if sql_answer.status != "ok" or sql_answer.signature != signature:
                raise SystemExit(f"could not precompute {signature}: {sql_answer.message}")
            answers[signature] = {
                "message": sql_answer.message,
                "sql": sql_answer.sql,
                "definition": sql_answer.definition,
                "table": sql_answer.table,
            }
            if index % 100 == 0:
                print(f"  precomputed {index} of {len(plans)}")
        print(f"  precomputed {len(plans)} answers")

        approval = ask("approval rate by payer", use_llm=False, connection=connection, catalog=catalog)
        decision = ask("median hours to decision by payer", use_llm=False, connection=connection, catalog=catalog)
        by_payer = _merge_breakdown(approval.table, decision.table)
        by_drug = _merge_breakdown(
            ask("approval rate by drug", use_llm=False, connection=connection, catalog=catalog).table,
            ask("median hours to decision by drug", use_llm=False, connection=connection, catalog=catalog).table,
        )
        by_channel = _merge_breakdown(
            ask("approval rate by channel", use_llm=False, connection=connection, catalog=catalog).table,
            ask("median hours to decision by channel", use_llm=False, connection=connection, catalog=catalog).table,
        )
        rate_row = ask("What is the approval rate?", use_llm=False, connection=connection, catalog=catalog).rows[0]
        hour_row = ask("median hours to decision", use_llm=False, connection=connection, catalog=catalog).rows[0]
        abandon_row = ask("abandonment rate", use_llm=False, connection=connection, catalog=catalog).rows[0]
        stuck_row = ask("how many cases are stuck", use_llm=False, connection=connection, catalog=catalog).rows[0]
        headlines = [
            {
                "label": "Approval rate",
                "value": pct(as_float(rate_row["approval_rate"])),
                "note": (
                    f"{commas(as_int(rate_row['approval_numerator']))} of "
                    f"{commas(as_int(rate_row['approval_denominator']))} decided cases"
                ),
            },
            {
                "label": "Median hours to decision",
                "value": hours_label(as_float(hour_row["median_hours_to_decision"])),
                "note": (
                    f"{commas(as_int(hour_row['decision_duration_n']))} cases with a submission and a decision"
                ),
            },
            {
                "label": "Abandonment rate",
                "value": pct(as_float(abandon_row["abandonment_rate"])),
                "note": (
                    f"{commas(as_int(abandon_row['abandon_numerator']))} of "
                    f"{commas(as_int(abandon_row['abandon_denominator']))} cases that reached prior authorization and then closed"
                ),
            },
            {
                "label": "Cases stuck",
                "value": commas(as_int(stuck_row["cases_stuck"])),
                "note": "Open longer than the stage SLA",
            },
        ]
        intro = (
            f"These numbers are from one pipeline run for Lakeshore Specialty Clinic. "
            f"The history runs from {_date_label(behavior['window_start'])} through {_date_label(behavior['window_end'])}. "
            f"Open cases are measured at {_date_label(behavior['as_of'])}. "
            f"There are {commas(as_int(mart['cases']))} cases in this clinic slice. "
            "Patients, prescribers, and events are synthetic. Payer behavior in the generator is synthetic. "
            "Drug names and package NDCs are real public identifiers."
        )
        definitions = [
            {"name": metric.name, "text": metric.definition} for metric in catalog.metrics.values()
        ]
        for blob in (intro, MISSING, *(card["value"] for card in headlines), *(card["note"] for card in headlines)):
            assert_plain(blob, "page")
        for item in definitions:
            assert_plain(item["text"], item["name"])
        fixtures = []
        for question in FIXTURES:
            parsed = parse_question(question, catalog, role="clinic")
            if parsed.refusal:
                fixtures.append(
                    {
                        "question": question,
                        "status": "refuse",
                        "code": parsed.refusal.code,
                        "signature": None,
                        "message": parsed.refusal.message,
                    }
                )
            else:
                fixtures.append(
                    {
                        "question": question,
                        "status": "ok",
                        "code": None,
                        "signature": parsed.plan.signature(),
                        "message": None,
                    }
                )
        payload = {
            "lexicon": parser_lexicon(catalog),
            "answers": answers,
            "fixtures": fixtures,
            "page": {
                "intro": intro,
                "missing_message": MISSING,
                "headlines": headlines,
                "breakdowns": {"payer": by_payer, "drug": by_drug, "channel": by_channel},
                "definitions": definitions,
                "chips": [
                    "What is the approval rate?",
                    "Median hours to decision by payer",
                    "Approval rate for Wegovy",
                    "Where are cases stuck?",
                    "What is our revenue?",
                ],
                **_funnel_block(funnel_row),
            },
        }
    finally:
        connection.close()

    SITE.mkdir(exist_ok=True)
    text = "window.DEMO = " + json.dumps(payload, indent=2) + ";\n"
    (SITE / "data.js").write_text(text)
    print(f"wrote {SITE / 'data.js'} ({len(text):,} bytes, {len(answers)} answers)")


def _question_for(plan: Plan, catalog) -> str:
    """A question the deterministic parser maps back to this plan."""
    metric = catalog.metrics[plan.metric_id]
    alias = metric.aliases[0]
    parts = [alias]
    if plan.group_by:
        word = {
            "payer_name": "payer",
            "drug_name": "drug",
            "channel": "channel",
            "line_of_business": "line of business",
            "drug_class": "drug class",
            "priority": "priority",
            "coverage_path": "coverage path",
            "case_month": "month",
            "stuck_stage": "stage",
        }[plan.group_by]
        parts.append("by " + word)
    for dimension, value in plan.filters:
        choices = [
            alias
            for item in catalog.values
            if item.dimension == dimension and item.value == value
            for alias in item.aliases
        ]
        parts.append("for " + max(choices, key=len))
    if plan.metric_id == "cases_stuck" and plan.group_by == "stuck_stage" and not plan.filters:
        return "where are cases stuck"
    return " ".join(parts)


if __name__ == "__main__":
    main()
