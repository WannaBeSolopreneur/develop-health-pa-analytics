"""Fill the README results block from the latest dbt run and the warehouse."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pa_model.agent.ask import run_sql
from pa_model.agent.catalog import ROOT
from pa_model.agent.present import as_float, as_int, commas, hours_label, pct

README = ROOT / "README.md"
START = "<!-- RESULTS:START -->"
END = "<!-- RESULTS:END -->"


def main() -> None:
    results = json.loads((ROOT / "dbt" / "target" / "run_results.json").read_text())["results"]
    counts = Counter(item["status"] for item in results)
    kinds = Counter(item["unique_id"].split(".", 1)[0] for item in results)
    succeeded = counts["pass"] + counts["success"]
    headline = run_sql(
        "select * from marts.mart_headline_metrics where clinic_id = 'clinic_lakeshore'"
    )[0]
    funnel = run_sql("select * from marts.mart_pa_funnel where clinic_id = 'clinic_lakeshore'")[0]
    quality = run_sql("select * from marts.mart_data_quality")[0]
    clinics = run_sql(
        """
        select clinic_name, count(*) as cases
        from marts.fct_pa_cases
        where is_qa_fixture = 0
        group by 1
        order by 2 desc
        """
    )
    lines = [
        "",
        (
            f"dbt build: {succeeded} of {len(results)} succeeded "
            f"({kinds['model']} models, {kinds['seed']} seeds, {kinds['test']} data tests). "
            f"{counts['warn']} warn, {counts['error']} error, {counts['skip']} skip."
        ),
        "Routing tests, the agent smoke test, and the page parser test run in `make all` before this block is written. A failure stops the command.",
        "",
        "Lakeshore Specialty Clinic, QA fixtures removed:",
        "",
        f"- Cases: {commas(as_int(headline['cases']))}",
        (
            f"- Approval rate: {pct(as_float(headline['approval_rate']))} "
            f"({commas(as_int(headline['approval_numerator']))} of {commas(as_int(headline['approval_denominator']))} decided cases)"
        ),
        (
            f"- First pass approval rate: {pct(as_float(headline['first_pass_approval_rate']))} "
            f"({commas(as_int(headline['first_pass_numerator']))} of {commas(as_int(headline['first_pass_denominator']))})"
        ),
        (
            f"- Median hours to decision: {hours_label(as_float(headline['median_hours_to_decision']))} "
            f"across {commas(as_int(headline['decision_duration_n']))} cases with a submission and a decision"
        ),
        (
            f"- Time to therapy: {hours_label(as_float(headline['median_hours_to_therapy']))} "
            f"across {commas(as_int(headline['therapy_duration_n']))} approved cases"
        ),
        (
            f"- Abandonment rate: {pct(as_float(headline['abandonment_rate']))} "
            f"({commas(as_int(headline['abandon_numerator']))} of {commas(as_int(headline['abandon_denominator']))})"
        ),
        (
            f"- Appeal overturn rate: {pct(as_float(headline['appeal_overturn_rate']))} "
            f"({commas(as_int(headline['appeal_numerator']))} of {commas(as_int(headline['appeal_denominator']))})"
        ),
        f"- Cases stuck: {commas(as_int(headline['cases_stuck']))}",
        (
            "- Funnel: "
            f"{commas(as_int(funnel['cases_started']))} started, "
            f"{commas(as_int(funnel['cases_benefit_verified']))} benefit verified, "
            f"{commas(as_int(funnel['cases_pa_required']))} PA required, "
            f"{commas(as_int(funnel['cases_submitted']))} submitted, "
            f"{commas(as_int(funnel['cases_decided_after_submit']))} decided after submission, "
            f"{commas(as_int(funnel['cases_approved']))} approved"
        ),
        "",
        "Mess the models are built to absorb, from `mart_data_quality`:",
        "",
        (
            f"- Duplicate events {commas(as_int(quality['duplicate_events']))}, "
            f"late events {commas(as_int(quality['late_events']))}, "
            f"decision corrections {commas(as_int(quality['decision_corrections']))}, "
            f"missing event timestamps {commas(as_int(quality['missing_event_timestamps']))}, "
            f"chronology errors {commas(as_int(quality['chronology_errors']))}, "
            f"unmapped denial text {commas(as_int(quality['unmapped_denial_text']))}, "
            f"duplicate case rows {commas(as_int(quality['duplicate_case_rows']))}, "
            f"dirty diagnoses {commas(as_int(quality['dirty_diagnoses']))}"
        ),
        "",
        "Cases by tenant, QA fixtures removed:",
        "",
    ]
    for row in clinics:
        lines.append(f"- {row['clinic_name']}: {commas(as_int(row['cases']))}")
    lines.append("")
    block = "\n".join(lines)
    readme = README.read_text()
    if START not in readme or END not in readme:
        raise SystemExit("README is missing the results markers")
    before, rest = readme.split(START, 1)
    _old, after = rest.split(END, 1)
    README.write_text(before + START + "\n" + block + END + after)
    print("updated README results")


if __name__ == "__main__":
    main()
