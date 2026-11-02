"""Checks the agent against the warehouse. Refusals must not invent a number."""

from __future__ import annotations

import json
from pathlib import Path

from pa_model.agent.ask import ask, plan_from_mapping, run_sql
from pa_model.agent.catalog import ROOT, load_catalog
from pa_model.agent.present import as_float
from pa_model.agent.sql import render_sql

SITE = ROOT / "site" / "data.js"


def fail(message: str) -> None:
    raise SystemExit(message)


def near(left, right, label: str) -> None:
    if left is None or right is None or abs(float(left) - float(right)) > 1e-9:
        fail(f"{label} drifted: agent {left} warehouse {right}")


def main() -> None:
    catalog = load_catalog()
    mart = run_sql(
        "select * from marts.mart_headline_metrics where clinic_id = 'clinic_lakeshore'"
    )[0]
    approval = ask("What is the approval rate?", use_llm=False, catalog=catalog)
    if approval.status != "ok" or approval.interpreter != "parser":
        fail("approval rate did not resolve through the parser")
    near(approval.rows[0]["approval_rate"], mart["approval_rate"], "approval rate")
    near(approval.rows[0]["approval_numerator"], mart["approval_numerator"], "approval numerator")
    if "clinic_id = 'clinic_lakeshore'" not in approval.sql or "is_qa_fixture = 0" not in approval.sql:
        fail("clinic SQL is missing the tenant predicate")
    if "patient" in approval.sql.lower():
        fail("clinic SQL touched a patient column")

    first = ask("first pass approval rate", use_llm=False, catalog=catalog)
    near(first.rows[0]["first_pass_approval_rate"], mart["first_pass_approval_rate"], "first pass")
    hours = ask("median hours to decision", use_llm=False, catalog=catalog)
    near(hours.rows[0]["median_hours_to_decision"], mart["median_hours_to_decision"], "median hours")
    stuck = ask("how many cases are stuck", use_llm=False, catalog=catalog)
    near(stuck.rows[0]["cases_stuck"], mart["cases_stuck"], "cases stuck")
    counted = ask("how many cases", use_llm=False, catalog=catalog)
    near(counted.rows[0]["case_count"], mart["cases"], "case count")

    by_channel = ask("median hours to decision by channel", use_llm=False, catalog=catalog)
    fax = next(row for row in by_channel.rows if row["channel"] == "fax")
    direct_fax = run_sql(
        """
        select median(hours_to_decision) as median_hours
        from marts.fct_pa_cases
        where is_qa_fixture = 0
          and clinic_id = 'clinic_lakeshore'
          and channel = 'fax'
        """
    )[0]
    near(fax["median_hours_to_decision"], direct_fax["median_hours"], "fax median")

    for question in (
        "what is our revenue this quarter",
        "approval rate for zepbound",
        "approval rate for unitedhealthcare",
        "approval rate at Harbor Endocrine",
        "approval rate across all clinics",
        "what is the average bmi",
        "approval rate for aspirin",
    ):
        refused = ask(question, use_llm=False, catalog=catalog)
        if refused.status != "refuse" or refused.sql or refused.rows:
            fail(f"expected a refusal with no SQL for: {question}")

    harbor = ask("approval rate at Harbor Endocrine Associates", role="internal", use_llm=False, catalog=catalog)
    if harbor.status != "ok" or "clinic_id = 'clinic_harbor'" not in harbor.sql:
        fail("internal role did not filter Harbor")
    harbor_direct = run_sql(
        """
        select sum(is_final_approved) * 1.0 / nullif(sum(is_decision_denominator), 0) as approval_rate
        from marts.fct_pa_cases
        where is_qa_fixture = 0 and clinic_id = 'clinic_harbor'
        """
    )[0]
    near(harbor.rows[0]["approval_rate"], harbor_direct["approval_rate"], "harbor approval")

    everyone = ask("approval rate across all clinics", role="internal", use_llm=False, catalog=catalog)
    if everyone.status != "ok" or "clinic_id" in everyone.sql:
        fail("internal all-clinic question should not add a clinic predicate")

    planted = plan_from_mapping(
        {
            "metric_id": "approval_rate",
            "group_by": None,
            "filters": [],
            "approval_rate": 0.01,
            "answer": 1,
        },
        catalog,
        "clinic",
    )
    if planted.plan is None:
        fail("a catalog selection with an extra number should still be a plan")
    planted_sql = render_sql(planted.plan)
    planted_row = run_sql(planted_sql)[0]
    near(planted_row["approval_rate"], mart["approval_rate"], "planted number was used")
    if abs(as_float(planted_row["approval_rate"]) - 0.01) < 1e-9:
        fail("the planted 1% rate matched the warehouse, so this check is not informative")
    unknown = plan_from_mapping({"metric_id": "revenue", "filters": []}, catalog, "clinic")
    if unknown.plan is not None:
        fail("an unknown metric id was accepted")

    payload = json.loads(SITE.read_text().removeprefix("window.DEMO = ").removesuffix(";\n"))
    page_message = payload["answers"]["approval_rate||"]["message"]
    if page_message != approval.message:
        fail("the page snapshot does not match a live approval-rate answer")
    if "65" not in page_message and "approval" not in page_message.lower():
        fail("approval answer text looks empty")
    visible = _visible_html(ROOT / "site" / "index.html")
    for bad in (";", "—", "–"):
        if bad in visible:
            fail(f"page prose contains {bad!r}")
    print("agent smoke passed")


def _visible_html(path: Path) -> str:
    import re

    text = path.read_text()
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


if __name__ == "__main__":
    main()
