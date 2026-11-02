select
    clinic_id,
    clinic_name,
    count(*) as cases,
    sum(is_decision_denominator) as approval_denominator,
    sum(is_final_approved) as approval_numerator,
    case
        when sum(is_decision_denominator) = 0 then null
        else sum(is_final_approved) * 1.0 / sum(is_decision_denominator)
    end as approval_rate,
    sum(is_first_pass_denominator) as first_pass_denominator,
    sum(is_first_pass_approved) as first_pass_numerator,
    case
        when sum(is_first_pass_denominator) = 0 then null
        else sum(is_first_pass_approved) * 1.0 / sum(is_first_pass_denominator)
    end as first_pass_approval_rate,
    median(hours_to_decision) as median_hours_to_decision,
    sum(case when hours_to_decision is not null then 1 else 0 end) as decision_duration_n,
    median(hours_to_therapy) as median_hours_to_therapy,
    sum(case when hours_to_therapy is not null then 1 else 0 end) as therapy_duration_n,
    sum(is_abandon_denominator) as abandon_denominator,
    sum(is_abandoned) as abandon_numerator,
    case
        when sum(is_abandon_denominator) = 0 then null
        else sum(is_abandoned) * 1.0 / sum(is_abandon_denominator)
    end as abandonment_rate,
    sum(is_appeal_denominator) as appeal_denominator,
    sum(is_appeal_overturned) as appeal_numerator,
    case
        when sum(is_appeal_denominator) = 0 then null
        else sum(is_appeal_overturned) * 1.0 / sum(is_appeal_denominator)
    end as appeal_overturn_rate,
    sum(is_stuck) as cases_stuck
from {{ ref('fct_pa_cases') }}
where is_qa_fixture = 0
group by 1, 2
