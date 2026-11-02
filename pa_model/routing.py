"""Coverage routing grounded in public Medicare rules.

Probabilities are not decided here. This module only chooses the path:
commercial PA, Medicaid PA, Part D PA, Part D statutory exclusion, or the
Medicare GLP-1 Bridge.

Sources (retrieved 2026-11-02):

- Social Security Act section 1860D-2(e)(2) and CMS Excluded Drug Reference
  File FAQ. Part D does not cover drugs when used for weight loss.
  https://www.cms.gov/files/document/excluded-drug-reference-file-faq.pdf
- CMS, "Part D Coverage of Anti-Obesity Medications with Medically Accepted
  Indications" (HPMS, March 20, 2024), as reported by CMS and summarized by
  KFF and the Congressional Research Service. Wegovy can be a Part D drug
  when prescribed to reduce cardiovascular risk, not when prescribed for
  weight loss alone.
  https://www.kff.org/medicare/a-new-use-for-wegovy-opens-the-door-to-medicare-coverage-for-millions-of-people-with-obesity/
  https://www.congress.gov/crs_external_products/IF/PDF/IF12758/IF12758.2.pdf
- CMS Medicare GLP-1 Bridge, information for Part D plans. The demonstration
  runs July 1, 2026 through December 31, 2027. It covers Foundayo, Wegovy,
  and Zepbound KwikPen for weight management outside the Part D payment
  flow. Single-dose Zepbound pens and vials are not included. Type 2
  diabetes, moderate to severe obstructive sleep apnea, and noncirrhotic
  MASH with fibrosis stay on the Part D plan and are not bridge eligible.
  https://www.cms.gov/medicare/coverage/prescription-drug-coverage/medicare-glp-1-bridge/information-part-d-plans
  https://www.medicare.gov/coverage/weight-loss-drugs

Assumption. The diagnosis on the case is the prescriber's stated purpose.
A weight-management primary diagnosis with a cardiovascular secondary
diagnosis can meet the bridge BMI rule. A primary cardiovascular, diabetes,
sleep apnea, or MASH diagnosis stays on Part D.
"""

from __future__ import annotations

from datetime import datetime

BRIDGE_START = datetime(2026, 7, 1)

# Published bridge package NDCs. CMS list, April 6, 2026 update on the
# Part D plan page above. The generator only marks a drug bridge_eligible
# when its package NDC is on this list.
BRIDGE_PACKAGE_NDCS = {
    "0002-4178-31",
    "0002-4503-31",
    "0002-4794-31",
    "0002-4803-31",
    "0002-4839-31",
    "0002-4953-31",
    "0169-4525-14",
    "0169-4505-14",
    "0169-4501-14",
    "0169-4517-14",
    "0169-4524-14",
    "0169-4415-31",
    "0169-4404-31",
    "0169-4409-31",
    "0169-4425-31",
    "0169-4572-14",
    "0002-3566-11",
    "0002-3555-11",
    "0002-3544-11",
    "0002-3533-11",
    "0002-3522-11",
    "0002-3511-11",
}

HFPEF_OR_CKD = {"I50.32", "N18.31", "N18.32", "N18.4", "N18.5"}
BMI_27_COMORBID = {"R73.03", "I25.2", "I63.9", "I70.209"}
PART_D_EXACT = {"G47.33", "K75.81"}


def is_part_d_indication(drug: dict, primary_dx: str) -> bool:
    """True when the stated diagnosis should stay on the Part D plan."""
    dx = (primary_dx or "").upper()
    drug_class = drug["drug_class"]
    if drug_class not in {"glp1_diabetes", "glp1_weight"}:
        return False
    if dx.startswith("E11"):
        return True
    if dx in PART_D_EXACT:
        return True
    if drug["drug_id"] == "drug_wegovy" and dx.startswith(("I21", "I25", "I63", "I70")):
        return True
    return False


def meets_bridge_criteria(
    *,
    bmi: float | None,
    age_years: int,
    lifestyle_attested: int,
    secondary_dx: str | None,
    uncontrolled_hypertension: int,
) -> bool:
    """CMS bridge clinical criteria, applied to synthetic chart fields.

    The public rule is an attestation of BMI and comorbidities at the start
    of GLP-1 therapy, plus lifestyle modification. This dataset stores those
    fields directly. A missing BMI or a missing lifestyle attestation fails.
    """
    if bmi is None or age_years < 18 or lifestyle_attested != 1:
        return False
    if bmi >= 35:
        return True
    sec = (secondary_dx or "").upper()
    if bmi >= 30 and (sec in HFPEF_OR_CKD or (sec == "I10" and uncontrolled_hypertension == 1)):
        return True
    if bmi >= 27 and sec in BMI_27_COMORBID:
        return True
    return False


def route_case(
    *,
    drug: dict,
    line_of_business: str,
    primary_dx: str,
    initiated_at: datetime,
    bmi: float | None,
    age_years: int,
    lifestyle_attested: int,
    secondary_dx: str | None,
    uncontrolled_hypertension: int,
) -> tuple[str, str | None]:
    """Return (coverage_path, payer_id override).

    The override is the Medicare GLP-1 Bridge payer when the case leaves
    the Part D plan. Otherwise None, and the case keeps its plan.
    """
    medicare = line_of_business in {"part_d", "medicare_advantage"}
    if drug["drug_class"] in {"glp1_diabetes", "glp1_weight"} and medicare:
        if is_part_d_indication(drug, primary_dx):
            return "part_d_pa", None
        if (
            drug["bridge_eligible"]
            and drug["package_ndc"] in BRIDGE_PACKAGE_NDCS
            and initiated_at >= BRIDGE_START
            and meets_bridge_criteria(
                bmi=bmi,
                age_years=age_years,
                lifestyle_attested=lifestyle_attested,
                secondary_dx=secondary_dx,
                uncontrolled_hypertension=uncontrolled_hypertension,
            )
        ):
            return "glp1_bridge", "payer_glp1_bridge"
        return "part_d_exclusion", None
    if line_of_business == "medicaid":
        return "medicaid_pa", None
    if medicare:
        return "part_d_pa", None
    return "commercial_pa", None


def indication_match(drug: dict, primary_dx: str, coverage_path: str) -> str:
    if coverage_path == "glp1_bridge":
        return "bridge_weight"
    if coverage_path == "part_d_exclusion":
        return "statutory_exclusion"
    if coverage_path == "covered_no_pa":
        return "on_label"
    dx = (primary_dx or "").upper()
    for prefix in drug["on_label_prefixes"]:
        if dx.startswith(prefix.upper()):
            return "on_label"
    return "off_label"
