with recomputed as (
    select
        clinic_id,
        sum(is_final_approved) as approval_numerator,
        sum(is_decision_denominator) as approval_denominator,
        sum(is_stuck) as cases_stuck
    from {{ ref('fct_pa_cases') }}
    where is_qa_fixture = 0
    group by 1
)

select h.clinic_id
from {{ ref('mart_headline_metrics') }} as h
inner join recomputed as r
    on h.clinic_id = r.clinic_id
where h.approval_numerator <> r.approval_numerator
    or h.approval_denominator <> r.approval_denominator
    or h.cases_stuck <> r.cases_stuck
    or abs(h.approval_rate - (r.approval_numerator * 1.0 / nullif(r.approval_denominator, 0))) > 0.0000001
