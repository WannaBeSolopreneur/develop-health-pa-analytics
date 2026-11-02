Hi Develop Health team, I built this to show how I would approach your Lead Analytics Engineer role.

It is a private portfolio demo for prior authorization analytics. The clinic page is synthetic. It is not a Develop Health product, and it does not describe your current systems.

## Run

```bash
make all
```

That command creates the virtualenv, checks the coverage routing rules, generates the synthetic feed, runs `dbt build` on DuckDB, exports the clinic page, runs the agent smoke test, and checks that the page parser matches the Python parser. SQL is written so the same models can run on Snowflake. The DuckDB profile is `dbt/profiles.yml`. `PA_DB_PATH` overrides the database file.

Open `site/index.html` in a browser. The numbers are in `site/data.js`, so the page does not need a server. GitHub Pages can publish the `site/` folder. This repo should stay private.

Ask a question from the shell:

```bash
.venv/bin/python -m pa_model.agent.ask "median hours to decision by channel"
```

The agent works with no API key. If `PA_LLM_API_KEY` or `OPENAI_API_KEY` is set, a language model may choose a catalog entry. It never supplies the number. The number still comes from the SQL. `PA_LLM_BASE_URL` and `PA_LLM_MODEL` override the endpoint and model.

## Architecture

```
assumptions/behavior.yml
pa_model/routing.py          public coverage rules
pa_model/generate.py         synthetic raw feed, with mess
        |
        v
data/pa.duckdb               schemas raw, staging, intermediate, marts, seed
        |
        v
dbt staging                  cast, dedupe, repair ICD-10
dbt intermediate             one row per event, then one case spine
dbt marts                    dimensions, fct_pa_cases, tenant slice, headlines, funnel
        |
        +--> semantic/metrics.yml + pa_model/agent   SQL only from the allow list
        |
        +--> site/data.js + site/index.html          Lakeshore Specialty Clinic
```

Snowflake row access policies are written in `dbt/analyses/snowflake_row_access_policies.sql`. They are not executed on DuckDB. The live demo enforces the same predicate in `fct_pa_cases_tenant` and in every clinic question:

```sql
where is_qa_fixture = 0
  and clinic_id = 'clinic_lakeshore'
```

An internal role can omit `clinic_id` or set it to one named clinic. A clinic role cannot. `IS_ROLE_IN_SESSION('PA_INTERNAL_ANALYTICS')` is the Snowflake version of that split. A pharmacy is a tenant on the same `clinic_id` key.

Date differences go through `hours_between`, which is `date_diff` on DuckDB and `datediff` on Snowflake. Month keys go through `month_key`. The case spine uses `qualify`, `median`, and `nullif`, which both engines support.

## Metric definitions

Flags live on `fct_pa_cases`. The agent may aggregate those flags. It may not select patient columns, and it may not invent a formula.

| Metric | Definition |
| --- | --- |
| Approval rate | Final outcome approved, divided by cases whose final outcome is approved or denied. Pending and abandoned cases are out. An approved appeal counts as approved. A plan exclusion denial is a decision. |
| First pass approval rate | Decided cases whose initial decision was an approval, with no information request and no appeal. Same denominator as approval rate. |
| Median hours to decision | Median calendar hours from `pa_submitted` to the effective initial decision. Cases with no submission are excluded. Calendar hours match the Medicare drug clocks. |
| Abandonment rate | Abandoned cases divided by cases that reached a prior authorization requirement and then closed as abandoned, approved, or denied. |
| Appeal overturn rate | Appeal decisions that are approvals, divided by cases with an appeal decision. |
| Time to therapy | Median calendar hours from the benefit check start to approval, on approved cases only. This is access approval. A pharmacy fill is outside this metric. |
| Cases stuck | Open cases whose hours in the current stage are greater than the stage SLA. Equal to the SLA is not stuck. The as of timestamp is `2026-11-02 00:00:00`, not the machine clock. |
| Case count | Cases in the slice after QA fixtures are removed. |

Final outcome uses an open appeal as pending, then the appeal outcome, then the latest recorded decision, then abandonment, then covered with no PA, then pending. The effective decision is the row with the latest `recorded_at`, so a correction beats the earlier decision.

The funnel is nested: approved, then decided after submission, then submitted, then PA required, then benefit verified, then started. Exits are separate columns: covered with no PA, denied before submission, and abandoned.

## Ask agent

`semantic/metrics.yml` is the allow list. `pa_model/agent/sql.py` holds the matching SQL expressions. A metric in one file and not the other will not load.

The deterministic parser normalizes the question, matches the longest metric alias, then one breakdown, then catalog values. Ambiguous names are refused. `zepbound` must be Zepbound KwikPen or the single-dose pen. `unitedhealthcare` must be the commercial PPO or the Medicare Advantage plan. `semaglutide` must be Ozempic or Wegovy.

Revenue, satisfaction, forecasts, denial-reason essays, and patient-level fields are refused. A clinic question about another clinic is refused. The refusal is the whole answer. There is no fallback number.

The static page uses the same parser in `site/ask.js` and looks up a precomputed answer. It can answer the clinic overall, one breakdown, or one filter. A defined question outside that set is refused on the page with an explanation, rather than estimated in the browser. The shell agent runs the SQL for any single breakdown plus filters the catalog allows.

## Data sources and assumptions

Drug identifiers were read on 2026-11-02.

- openFDA NDC Directory, `https://api.fda.gov/drug/ndc.json`. A listing is the labeler's submission. It is not by itself proof of approval or of coverage. `https://open.fda.gov/apis/drug/ndc/`
- RxNorm RxCUI by brand name, `https://rxnav.nlm.nih.gov/REST/rxcui.json?name=`
- Humira package `0074-3799-02` is the kit openFDA returned: a kit with a 0.8 mL syringe, 2 kits. It is not described here as 40 mg/0.4 mL.
- Foundayo (orforglipron) RxCUI `2739845`, package `0002-4794-31`.

Coverage routing is in `pa_model/routing.py`. Retrieved 2026-11-02.

- Social Security Act section 1860D-2(e)(2) and the CMS Excluded Drug Reference File FAQ. Part D does not cover a drug when used for weight loss. `https://www.cms.gov/files/document/excluded-drug-reference-file-faq.pdf`
- CMS HPMS memorandum "Part D Coverage of Anti-Obesity Medications with Medically Accepted Indications" (March 20, 2024), as reported by KFF and the Congressional Research Service. Wegovy can be Part D when prescribed to reduce cardiovascular risk, not for weight loss alone.
- CMS Medicare GLP-1 Bridge for Part D plans. The demonstration runs July 1, 2026 through December 31, 2027, outside the Part D payment flow. Eligible products in this model are the package NDCs on that page for Foundayo, Wegovy, and Zepbound KwikPen. The single-dose Zepbound pen is not included. Ozempic, Mounjaro, and Trulicity are not on the bridge list. `https://www.cms.gov/medicare/coverage/prescription-drug-coverage/medicare-glp-1-bridge/information-part-d-plans`
- Bridge clinical criteria used here: age 18 or older, a lifestyle attestation, and BMI at least 35, or BMI at least 30 with HFpEF, CKD stage 3a or higher, or uncontrolled hypertension, or BMI at least 27 with prediabetes, old MI, stroke, or PAD. A missing BMI, a missing attestation, or age under 18 fails.
- Assumption. The primary diagnosis is the stated purpose. Type 2 diabetes, obstructive sleep apnea, MASH, and a Wegovy cardiovascular primary diagnosis stay on Part D and are not sent to the bridge. Commercial and Medicaid are never the bridge.
- The CMS page names Humana as the LI NET central processor. The payer row in this model is called Medicare GLP-1 Bridge so it is not confused with Humana Medicare Advantage. Bridge cases also get `bridge_claim_denied` and `plan_redirect_noted`, because the published flow starts from a denied pharmacy claim. `source_payer_id` keeps the original plan.
- Assumption. A share of commercial Trulicity cases are covered with no PA. This dataset does not read a formulary file. The share is in `assumptions/behavior.yml`.

Clocks, from `dbt/seeds/sla_thresholds.csv`:

- Part D decision: 72 hours standard and 24 hours expedited (42 CFR 423.568 and 423.572). Medicare Advantage drug PA uses those Part D clocks in this model.
- Medicaid covered outpatient drugs: 24 hours (Social Security Act 1927(d)(5), 42 CFR 438.3(s)(6)).
- CMS-0057-F medical prior authorization clocks exclude drugs. The 7 day and 72 hour medical clocks are not used as the drug decision clock.
- Commercial and GLP-1 Bridge decision SLAs are analytic assumptions aligned to the Part D clocks. They are not statutes.
- Benefit check, not yet submitted, and waiting on information are analytic assumptions.
- Part D appeal: 168 hours standard and 72 hours expedited (42 CFR 423.590). Commercial appeal hours are an assumption. The bridge page did not publish a decision clock.

Behavior probabilities, channel mix, expedited share, and the shape of the mess are synthetic. They live in `assumptions/behavior.yml` with seed `20261102`. They are not Develop Health rates and they are not measured payer performance. The AMA 2024 prior authorization survey is not a calibration target for these rates.

Other assumptions:

- Eight tenants. Lakeshore Specialty Clinic in Chicago is the featured clinic, about 18% of 36,000 generated cases. River City Specialty Pharmacy is a tenant on the same key.
- Window: 2025-11-01 00:00 through 2026-11-01 18:00. Adults only, birth years 1946 through 2006.
- Payer names are real organizations except Prairie State Medicaid MCO, which is fictional so the demo does not invent a state Medicaid policy.
- Prescriber NPIs are synthetic and fail the NPI check digit on purpose.
- ICD-10 codes are real codes. Some cases are off-label on purpose. Dirty codes have the dot removed or the case folded, and staging repairs them.
- Mess in the raw feed: duplicate events, late arrivals, decision corrections, missing `event_at`, decisions stamped before submission, duplicate case rows, duplicate patients, and denial text that is either free text from a known phrase or the unmapped phrase "see scanned notes".
- A decision correction is only added when the case already has a submission. A missing `event_at` is not combined with a late arrival on the same event. Staging fills a null `event_at` from `recorded_at`, and that combination was pushing the benefit check past the approval.
- Patients, prescribers, and events are synthetic. Do not treat a rate on the page as a real payer's performance.

## Test results

<!-- RESULTS:START -->

dbt build: 99 of 99 succeeded (23 models, 2 seeds, 74 data tests). 0 warn, 0 error, 0 skip.
Routing tests, the agent smoke test, and the page parser test run in `make all` before this block is written. A failure stops the command.

Lakeshore Specialty Clinic, QA fixtures removed:

- Cases: 6,482
- Approval rate: 65.5% (3,651 of 5,574 decided cases)
- First pass approval rate: 45.0% (2,509 of 5,574)
- Median hours to decision: 46.3 hours across 5,323 cases with a submission and a decision
- Time to therapy: 77.1 hours across 3,651 approved cases
- Abandonment rate: 11.7% (699 of 5,998)
- Appeal overturn rate: 40.7% (246 of 604)
- Cases stuck: 109
- Funnel: 6,482 started, 6,467 benefit verified, 6,132 PA required, 5,626 submitted, 5,323 decided after submission, 3,651 approved

Mess the models are built to absorb, from `mart_data_quality`:

- Duplicate events 4,863, late events 8,080, decision corrections 341, missing event timestamps 140, chronology errors 135, unmapped denial text 211, duplicate case rows 544, dirty diagnoses 306

Cases by tenant, QA fixtures removed:

- Lakeshore Specialty Clinic: 6,482
- River City Specialty Pharmacy: 4,938
- Harbor Endocrine Associates: 4,770
- Ironwood Health System Clinics: 4,421
- Cedar Dermatology Group: 4,268
- Northstar Rheumatology: 3,925
- Magnolia Allergy and Asthma: 3,604
- Summit GI Partners: 3,592
<!-- RESULTS:END -->

Custom data tests, beyond unique, not null, relationships, and accepted values:

- `assert_qa_fixtures` checks the hand-built cases: a later denial wins, a late event uses its own clock, a chronology error is repaired, a duplicate case keeps the latest channel, a dirty diagnosis is repaired, and a duplicate event collapses.
- `assert_durations_and_order` rejects negative clocks, a decision before submission, and an approval with no submission.
- `assert_funnel_is_nested` keeps the funnel subset order.
- `assert_stuck_past_sla` requires stuck cases to be open and past the SLA.
- `assert_metric_bounds` keeps rates between 0 and 1.
- `assert_headline_matches_fact` recomputes the headline from the fact.
- `assert_tenant_is_one_clinic` checks the Lakeshore slice.
- `assert_bridge_rules` checks bridge drugs, dates, diagnoses, and package NDCs.
- `assert_population_quality` checks adult birth dates, synthetic NPIs, enough mess to matter, and that every staged case reaches the fact.

QA fixtures stay on `fct_pa_cases` with `is_qa_fixture = 1` and are excluded from metrics and from the clinic page.

## Repo layout

```
Makefile
README.md
requirements.txt
assumptions/behavior.yml
semantic/metrics.yml
pa_model/                  generator, routing, agent, page export
dbt/models/staging
dbt/models/intermediate
dbt/models/marts
dbt/seeds
dbt/tests
dbt/macros
dbt/analyses/snowflake_row_access_policies.sql
site/index.html            static clinic page
site/data.js               precomputed answers from the pipeline
```

`data/raw`, the DuckDB file, and `dbt/target` are local build output.

## About me

Asad Moulvi, Data Scientist at DataQHealth. He builds healthcare data pipelines that process 10M+ records a year, covering claims, encounters, and patient attribution. He built a FHIR compliant AI scribe for doctors. He built Snowflake ETL that loaded a client's data into their Snowflake warehouse, and he holds a Snowflake certification. He built a PyTorch NLP system that summarizes cardiac device alerts and saves nurses 3 hours a day. MS in Business Analytics and AI, UT Dallas.

This demo is built in dbt.

GitHub: https://github.com/WannaBeSolopreneur
LinkedIn: https://www.linkedin.com/in/asad-moulvi-aaa278175
Email: asad.moulvi01@gmail.com
