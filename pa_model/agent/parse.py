"""Deterministic question parser.

A question becomes one metric, an optional group, and equality filters whose
values come from the catalog. Anything else is a refusal. The question text
is never copied into SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pa_model.agent.catalog import Catalog, normalize


@dataclass
class Plan:
    metric_id: str
    group_by: str | None
    filters: list[tuple[str, str]]
    clinic_id: str | None
    role: str

    def signature(self) -> str:
        filt = ",".join(f"{key}={value}" for key, value in self.filters)
        return f"{self.metric_id}|{self.group_by or ''}|{filt}"


@dataclass
class Refusal:
    message: str
    code: str


@dataclass
class ParseResult:
    plan: Plan | None = None
    refusal: Refusal | None = None
    mask_notes: list[str] = field(default_factory=list)


def _mask() -> list[bool]:
    return []


class SpanMask:
    def __init__(self, text: str) -> None:
        self.text = text
        self.hidden = [False] * len(text)

    def visible(self) -> str:
        chars = []
        for index, char in enumerate(self.text):
            chars.append(" " if self.hidden[index] else char)
        return "".join(chars)

    def cover(self, start: int, end: int) -> None:
        for index in range(start, end):
            self.hidden[index] = True

    def find_phrase(self, phrase: str) -> int | None:
        """Word-boundary match against the still-visible text. Returns start index."""
        if not phrase:
            return None
        haystack = self.visible()
        start = 0
        while True:
            index = haystack.find(phrase, start)
            if index < 0:
                return None
            end = index + len(phrase)
            before_ok = index == 0 or haystack[index - 1] == " "
            after_ok = end == len(haystack) or haystack[end] == " "
            if before_ok and after_ok:
                return index
            start = index + 1


def _choice_message(noun: str, choices: list[str]) -> str:
    quoted = ", ".join(choices)
    return f"That {noun} matches more than one value. Ask for one of these: {quoted}."


def _undefined_message(catalog: Catalog) -> str:
    names = ", ".join(metric.name.lower() for metric in catalog.metrics.values())
    return f"That question is not a defined metric. I will not invent a number. Defined metrics are {names}."


def parser_lexicon(catalog: Catalog) -> dict:
    """Sorted match tables. The page parser uses this same export."""
    clinic_aliases = []
    for clinic in catalog.clinics:
        for alias in clinic["aliases"]:
            clinic_aliases.append(
                {
                    "alias": alias,
                    "clinic_id": clinic["clinic_id"],
                    "name": clinic["name"],
                    "featured": clinic["featured"],
                }
            )
    clinic_aliases.sort(key=lambda item: len(item["alias"]), reverse=True)
    value_aliases = []
    for item in catalog.values:
        for alias in item.aliases:
            value_aliases.append({"alias": alias, "dimension": item.dimension, "value": item.value})
    value_aliases.sort(key=lambda item: len(item["alias"]), reverse=True)
    group_phrases = [
        {"phrase": phrase, "dimension": dimension}
        for phrase, dimension in sorted(catalog.group_aliases.items(), key=lambda item: len(item[0]), reverse=True)
    ]
    return {
        "metric_aliases": [{"alias": alias, "metric_id": metric_id} for alias, metric_id in catalog.metric_alias_pairs()],
        "clinic_aliases": clinic_aliases,
        "value_aliases": value_aliases,
        "group_phrases": group_phrases,
        "ambiguous": [
            {"phrase": normalize(item["phrase"]), "choices": item["choices"], "noun": item["noun"]}
            for item in catalog.ambiguous
        ],
        "blocked": [
            {"phrases": [normalize(phrase) for phrase in block["phrases"]], "message": block["message"]}
            for block in catalog.blocked
        ],
        "stopwords": sorted(catalog.stopwords),
        "compare_phrases": ["compared to", "versus", " vs "],
        "all_clinic_phrases": ["all clinics", "every clinic", "across clinics", "other clinics", "all tenants"],
        "metrics": {
            metric.metric_id: {"name": metric.name, "group_by": metric.group_by}
            for metric in catalog.metrics.values()
        },
        "featured_clinic_id": catalog.featured_clinic_id,
        "featured_clinic_name": catalog.featured_clinic_name,
        "undefined_message": _undefined_message(catalog),
    }


def parse_question(question: str, catalog: Catalog, role: str = "clinic") -> ParseResult:
    if role not in {"clinic", "internal"}:
        return ParseResult(refusal=Refusal("The role is not clinic or internal.", "role"))
    text = normalize(question)
    if not text:
        return ParseResult(refusal=Refusal(_undefined_message(catalog), "undefined"))

    for block in catalog.blocked:
        for phrase in block["phrases"]:
            if SpanMask(text).find_phrase(normalize(phrase)) is not None:
                return ParseResult(refusal=Refusal(block["message"], "blocked"))

    if any(phrase in text for phrase in ("compared to", "versus", " vs ")):
        return ParseResult(
            refusal=Refusal(
                "I will not compare two slices in one answer. Ask for each slice on its own.",
                "compare",
            )
        )

    lexicon = parser_lexicon(catalog)
    mask = SpanMask(text)
    featured = {"name": lexicon["featured_clinic_name"], "clinic_id": lexicon["featured_clinic_id"]}
    mentioned = []
    mentioned_ids = set()
    for clinic in lexicon["clinic_aliases"]:
        start = mask.find_phrase(clinic["alias"])
        if start is None:
            continue
        mask.cover(start, start + len(clinic["alias"]))
        if clinic["clinic_id"] not in mentioned_ids:
            mentioned.append(clinic)
            mentioned_ids.add(clinic["clinic_id"])

    all_clinics = any(phrase in text for phrase in lexicon["all_clinic_phrases"])
    other = [clinic for clinic in mentioned if not clinic["featured"]]
    if role == "clinic" and (all_clinics or other):
        if other:
            detail = f"{other[0]['name']} is outside that tenant."
        else:
            detail = "A question about every clinic is outside that tenant."
        return ParseResult(
            refusal=Refusal(
                (
                    f"This view is {featured['name']} only. {detail} "
                    "The clinic agent will not read it. In Snowflake a row access policy enforces the same boundary."
                ),
                "tenant",
            )
        )
    clinic_id = featured["clinic_id"] if role == "clinic" else None
    if role == "internal" and other:
        if len(other) > 1:
            return ParseResult(
                refusal=Refusal("Name one clinic. I will not blend tenants in one answer.", "tenant")
            )
        clinic_id = other[0]["clinic_id"]
    elif role == "internal" and any(clinic["featured"] for clinic in mentioned) and not all_clinics:
        clinic_id = featured["clinic_id"]

    metric_hits: list[tuple[int, int, str]] = []
    for item in lexicon["metric_aliases"]:
        start = mask.find_phrase(item["alias"])
        if start is None:
            continue
        metric_hits.append((start, start + len(item["alias"]), item["metric_id"]))
        mask.cover(start, start + len(item["alias"]))
    metric_ids = list(dict.fromkeys(metric_id for _start, _end, metric_id in metric_hits))
    if not metric_ids:
        return ParseResult(refusal=Refusal(_undefined_message(catalog), "undefined"))
    if len(metric_ids) > 1:
        return ParseResult(
            refusal=Refusal("That question names more than one metric. Ask for one metric.", "ambiguous_metric")
        )
    metric_id = metric_ids[0]
    metric = catalog.metrics[metric_id]

    group_by = None
    group_hits = []
    for item in lexicon["group_phrases"]:
        probe = "by " + item["phrase"]
        start = mask.find_phrase(probe)
        if start is None:
            continue
        group_hits.append((start, start + len(probe), item["dimension"]))
        mask.cover(start, start + len(probe))
    group_dims = list(dict.fromkeys(dimension for _s, _e, dimension in group_hits))
    if len(group_dims) > 1:
        return ParseResult(refusal=Refusal("Ask for one breakdown at a time.", "ambiguous_group"))
    if group_dims:
        group_by = group_dims[0]
    if metric_id == "cases_stuck" and group_by is None and ("where" in text or "stall" in text):
        group_by = "stuck_stage"
    if group_by is not None and group_by not in metric.group_by:
        return ParseResult(
            refusal=Refusal(
                f"{metric.name} cannot be broken down by that dimension. I will not invent the split.",
                "group",
            )
        )

    value_hits: list[tuple[str, str]] = []
    seen_spans = []
    for item in lexicon["value_aliases"]:
        start = mask.find_phrase(item["alias"])
        if start is None:
            continue
        end = start + len(item["alias"])
        if any(not (end <= left or start >= right) for left, right in seen_spans):
            continue
        seen_spans.append((start, end))
        mask.cover(start, end)
        value_hits.append((item["dimension"], item["value"]))

    for ambiguous in lexicon["ambiguous"]:
        if mask.find_phrase(ambiguous["phrase"]) is not None:
            return ParseResult(
                refusal=Refusal(_choice_message(ambiguous["noun"], ambiguous["choices"]), "ambiguous_value")
            )

    filters: list[tuple[str, str]] = []
    for dimension, value in value_hits:
        if dimension == "clinic_id":
            continue
        if any(existing == dimension for existing, _value in filters):
            label = dimension.replace("_", " ")
            return ParseResult(
                refusal=Refusal(
                    f"I can use one {label} filter at a time. I will not blend them.",
                    "multi_filter",
                )
            )
        if dimension not in metric.group_by and dimension != "stuck_stage":
            return ParseResult(
                refusal=Refusal(
                    f"{metric.name} cannot be filtered by that dimension. I will not invent the slice.",
                    "filter",
                )
            )
        if dimension == "stuck_stage" and dimension not in metric.group_by:
            return ParseResult(
                refusal=Refusal(
                    f"{metric.name} cannot be filtered by stage. Cases stuck can.",
                    "filter",
                )
            )
        filters.append((dimension, value))
    filters.sort()

    if group_by and any(dimension == group_by for dimension, _value in filters):
        return ParseResult(
            refusal=Refusal(
                "Ask for that value, or ask for the breakdown. I will not do both on the same dimension.",
                "group_filter",
            )
        )

    leftovers = []
    for token in mask.visible().split():
        if token and token not in catalog.stopwords:
            leftovers.append(token)
    if leftovers:
        sample = " ".join(leftovers[:6])
        return ParseResult(
            refusal=Refusal(
                f"I do not have a defined filter for {sample}. I will not ignore it and answer a broader question.",
                "unknown",
            )
        )

    return ParseResult(
        plan=Plan(
            metric_id=metric_id,
            group_by=group_by,
            filters=filters,
            clinic_id=clinic_id,
            role=role,
        )
    )
