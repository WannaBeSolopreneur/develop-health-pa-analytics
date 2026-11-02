"""Unit tests for public-rule routing. No database required."""

from __future__ import annotations

from datetime import datetime

from pa_model.reference import DRUGS
from pa_model.routing import meets_bridge_criteria, route_case

BY_ID = {d["drug_id"]: d for d in DRUGS}
AFTER = datetime(2026, 8, 15)
BEFORE = datetime(2026, 5, 2)


def route(**kwargs):
    base = dict(
        bmi=36,
        age_years=52,
        lifestyle_attested=1,
        secondary_dx=None,
        uncontrolled_hypertension=0,
    )
    base.update(kwargs)
    return route_case(**base)


def test_bridge_for_wegovy_weight_after_start() -> None:
    path, payer = route(
        drug=BY_ID["drug_wegovy"],
        line_of_business="part_d",
        primary_dx="E66.01",
        initiated_at=AFTER,
    )
    assert path == "glp1_bridge"
    assert payer == "payer_glp1_bridge"


def test_no_bridge_before_july_2026() -> None:
    path, payer = route(
        drug=BY_ID["drug_wegovy"],
        line_of_business="medicare_advantage",
        primary_dx="E66.01",
        initiated_at=BEFORE,
    )
    assert path == "part_d_exclusion"
    assert payer is None


def test_diabetes_stays_on_part_d() -> None:
    path, payer = route(
        drug=BY_ID["drug_wegovy"],
        line_of_business="part_d",
        primary_dx="E11.9",
        initiated_at=AFTER,
        bmi=40,
    )
    assert path == "part_d_pa"
    assert payer is None


def test_osa_and_mash_stay_on_part_d() -> None:
    for dx in ("G47.33", "K75.81"):
        path, _ = route(
            drug=BY_ID["drug_zepbound_kwikpen"],
            line_of_business="part_d",
            primary_dx=dx,
            initiated_at=AFTER,
        )
        assert path == "part_d_pa", dx


def test_wegovy_cvd_stays_on_part_d() -> None:
    path, _ = route(
        drug=BY_ID["drug_wegovy"],
        line_of_business="part_d",
        primary_dx="I25.10",
        initiated_at=AFTER,
    )
    assert path == "part_d_pa"


def test_single_dose_zepbound_is_not_on_the_bridge() -> None:
    path, _ = route(
        drug=BY_ID["drug_zepbound_pen"],
        line_of_business="part_d",
        primary_dx="E66.01",
        initiated_at=AFTER,
        bmi=42,
    )
    assert path == "part_d_exclusion"


def test_kwikpen_and_foundayo_can_use_the_bridge() -> None:
    for drug_id in ("drug_zepbound_kwikpen", "drug_foundayo"):
        path, payer = route(
            drug=BY_ID[drug_id],
            line_of_business="medicare_advantage",
            primary_dx="E66.9",
            initiated_at=AFTER,
        )
        assert path == "glp1_bridge", drug_id
        assert payer == "payer_glp1_bridge"


def test_ozempic_weight_use_is_exclusion_not_bridge() -> None:
    path, _ = route(
        drug=BY_ID["drug_ozempic"],
        line_of_business="part_d",
        primary_dx="E66.01",
        initiated_at=AFTER,
        bmi=40,
    )
    assert path == "part_d_exclusion"


def test_commercial_is_not_the_bridge() -> None:
    path, payer = route(
        drug=BY_ID["drug_wegovy"],
        line_of_business="commercial",
        primary_dx="E66.01",
        initiated_at=AFTER,
    )
    assert path == "commercial_pa"
    assert payer is None


def test_medicaid_is_not_the_bridge() -> None:
    path, _ = route(
        drug=BY_ID["drug_foundayo"],
        line_of_business="medicaid",
        primary_dx="E66.01",
        initiated_at=AFTER,
    )
    assert path == "medicaid_pa"


def test_bmi_bands() -> None:
    assert meets_bridge_criteria(
        bmi=35, age_years=40, lifestyle_attested=1, secondary_dx=None, uncontrolled_hypertension=0
    )
    assert not meets_bridge_criteria(
        bmi=34.9, age_years=40, lifestyle_attested=1, secondary_dx=None, uncontrolled_hypertension=0
    )
    assert meets_bridge_criteria(
        bmi=31, age_years=40, lifestyle_attested=1, secondary_dx="N18.31", uncontrolled_hypertension=0
    )
    assert not meets_bridge_criteria(
        bmi=31, age_years=40, lifestyle_attested=1, secondary_dx="I10", uncontrolled_hypertension=0
    )
    assert meets_bridge_criteria(
        bmi=31, age_years=40, lifestyle_attested=1, secondary_dx="I10", uncontrolled_hypertension=1
    )
    assert meets_bridge_criteria(
        bmi=27, age_years=40, lifestyle_attested=1, secondary_dx="I25.2", uncontrolled_hypertension=0
    )
    assert not meets_bridge_criteria(
        bmi=27, age_years=40, lifestyle_attested=1, secondary_dx=None, uncontrolled_hypertension=0
    )
    assert not meets_bridge_criteria(
        bmi=None, age_years=40, lifestyle_attested=1, secondary_dx=None, uncontrolled_hypertension=0
    )
    assert not meets_bridge_criteria(
        bmi=40, age_years=40, lifestyle_attested=0, secondary_dx=None, uncontrolled_hypertension=0
    )
    assert not meets_bridge_criteria(
        bmi=40, age_years=17, lifestyle_attested=1, secondary_dx=None, uncontrolled_hypertension=0
    )


def test_biologic_on_medicare_is_part_d_pa() -> None:
    path, _ = route(
        drug=BY_ID["drug_humira"],
        line_of_business="part_d",
        primary_dx="M06.9",
        initiated_at=AFTER,
        bmi=None,
        lifestyle_attested=0,
    )
    assert path == "part_d_pa"


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
    print(f"routing tests passed: {len(tests)}")


if __name__ == "__main__":
    main()
