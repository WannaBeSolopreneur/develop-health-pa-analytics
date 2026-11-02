"""Ask one question. Numbers come only from SQL that the catalog allows."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import duckdb

from pa_model.agent.catalog import ROOT, Catalog, load_catalog
from pa_model.agent.parse import ParseResult, Refusal, parse_question
from pa_model.agent.present import assert_plain, grouped_message, grouped_table, scalar_message
from pa_model.agent.sql import render_sql

DEFAULT_DB = ROOT / "data" / "pa.duckdb"


@dataclass
class Answer:
    status: str
    message: str
    code: str | None = None
    metric_id: str | None = None
    definition: str | None = None
    sql: str | None = None
    signature: str | None = None
    rows: list[dict] = field(default_factory=list)
    table: dict | None = None
    interpreter: str = "parser"


def _db_path() -> str:
    return os.environ.get("PA_DB_PATH", str(DEFAULT_DB))


def run_sql(sql: str, connection=None) -> list[dict]:
    own = connection is None
    if own:
        connection = duckdb.connect(_db_path(), read_only=True)
    try:
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        if own:
            connection.close()


def plan_from_mapping(payload: dict, catalog: Catalog, role: str) -> ParseResult:
    """Validate a language-model selection. Numeric fields are ignored."""
    if not isinstance(payload, dict):
        return ParseResult(refusal=Refusal("The language model did not return a catalog selection.", "llm"))
    metric_id = payload.get("metric_id")
    if metric_id not in catalog.metrics:
        return ParseResult(
            refusal=Refusal(
                "The language model selected a metric that is not defined. I will not answer.",
                "llm",
            )
        )
    metric = catalog.metrics[metric_id]
    group_by = payload.get("group_by") or None
    if group_by is not None and group_by not in metric.group_by:
        return ParseResult(
            refusal=Refusal("The language model asked for a breakdown that is not defined.", "llm")
        )
    raw_filters = payload.get("filters") or []
    if not isinstance(raw_filters, list):
        return ParseResult(refusal=Refusal("The language model filters were not a list.", "llm"))
    allowed = {(item.dimension, item.value) for item in catalog.values}
    filters = []
    for item in raw_filters:
        if not isinstance(item, dict):
            return ParseResult(refusal=Refusal("The language model filter was not a dimension value.", "llm"))
        pair = (item.get("dimension"), item.get("value"))
        if pair not in allowed:
            return ParseResult(
                refusal=Refusal("The language model used a filter value that is not in the catalog.", "llm")
            )
        if pair[0] not in metric.group_by:
            return ParseResult(
                refusal=Refusal("The language model filtered a dimension this metric does not allow.", "llm")
            )
        filters.append(pair)
    if len({dimension for dimension, _value in filters}) != len(filters):
        return ParseResult(refusal=Refusal("The language model repeated a dimension.", "llm"))
    clinic_id = catalog.featured_clinic_id if role == "clinic" else payload.get("clinic_id") or None
    if role == "clinic" and payload.get("clinic_id") not in (None, catalog.featured_clinic_id):
        return ParseResult(
            refusal=Refusal(
                f"This view is {catalog.featured_clinic_name} only. The clinic agent will not read another tenant.",
                "tenant",
            )
        )
    if clinic_id is not None and clinic_id not in {clinic["clinic_id"] for clinic in catalog.clinics}:
        return ParseResult(refusal=Refusal("The language model named a clinic that is not in the model.", "llm"))
    from pa_model.agent.parse import Plan

    filters.sort()
    return ParseResult(
        plan=Plan(
            metric_id=metric_id,
            group_by=group_by,
            filters=filters,
            clinic_id=clinic_id,
            role=role,
        )
    )


def _llm_payload(question: str, catalog: Catalog) -> dict | None:
    key = os.environ.get("PA_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    hint = {
        "metrics": [
            {"metric_id": metric.metric_id, "name": metric.name, "group_by": metric.group_by}
            for metric in catalog.metrics.values()
        ],
        "filters": [
            {"dimension": item.dimension, "value": item.value, "label": item.label} for item in catalog.values
        ],
        "clinics": [{"clinic_id": clinic["clinic_id"], "name": clinic["name"]} for clinic in catalog.clinics],
        "instruction": (
            "Choose metric_id, group_by or null, filters as a list of dimension and value, "
            "and clinic_id or null. Do not include any numeric answer."
        ),
        "question": question,
    }
    body = {
        "model": os.environ.get("PA_LLM_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You select a catalog entry. You never calculate or invent a number. Return JSON only.",
            },
            {"role": "user", "content": json.dumps(hint)},
        ],
    }
    url = os.environ.get("PA_LLM_BASE_URL", "https://api.openai.com/v1/chat/completions")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, ValueError):
        return None


def ask(
    question: str,
    role: str = "clinic",
    use_llm: bool | None = None,
    connection=None,
    catalog: Catalog | None = None,
) -> Answer:
    catalog = catalog or load_catalog()
    if use_llm is None:
        use_llm = bool(os.environ.get("PA_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    interpreter = "parser"
    parsed = parse_question(question, catalog, role=role)
    if use_llm:
        proposed = _llm_payload(question, catalog)
        if proposed is not None:
            checked = plan_from_mapping(proposed, catalog, role)
            if checked.plan is not None:
                parsed = checked
                interpreter = "llm"
    if parsed.refusal is not None:
        assert_plain(parsed.refusal.message, "refusal")
        return Answer(status="refuse", message=parsed.refusal.message, code=parsed.refusal.code, interpreter=interpreter)
    plan = parsed.plan
    assert plan is not None
    sql = render_sql(plan)
    rows = run_sql(sql, connection)
    if plan.group_by:
        message = grouped_message(plan, catalog)
        table = grouped_table(plan, rows, catalog)
    else:
        message = scalar_message(plan, rows[0] if rows else {}, catalog)
        table = None
    definition = catalog.metrics[plan.metric_id].definition
    assert_plain(message, "answer")
    assert_plain(definition, "definition")
    return Answer(
        status="ok",
        message=message,
        metric_id=plan.metric_id,
        definition=definition,
        sql=sql,
        signature=plan.signature(),
        rows=rows,
        table=table,
        interpreter=interpreter,
    )


def main() -> None:
    import sys

    question = " ".join(sys.argv[1:]).strip() or "What is the approval rate?"
    answer = ask(question, use_llm=False)
    print(answer.message)
    if answer.sql:
        print()
        print(answer.sql)
    if answer.definition:
        print()
        print(answer.definition)


if __name__ == "__main__":
    main()
