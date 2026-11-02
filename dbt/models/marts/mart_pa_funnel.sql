select
    clinic_id,
    count(*) as cases_started,
    sum(reached_benefit_verified) as cases_benefit_verified,
    sum(reached_pa_required) as cases_pa_required,
    sum(reached_submitted) as cases_submitted,
    sum(reached_decision_after_submit) as cases_decided_after_submit,
    sum(is_final_approved) as cases_approved,
    sum(case when final_outcome = 'covered_no_pa' then 1 else 0 end) as cases_covered_no_pa,
    sum(case when final_outcome = 'denied' and submitted_at is null then 1 else 0 end) as cases_denied_before_submission,
    sum(case when final_outcome = 'abandoned' then 1 else 0 end) as cases_abandoned,
    sum(case when is_stuck = 1 and stuck_stage = 'benefit_check' then 1 else 0 end) as stuck_benefit_check,
    sum(case when is_stuck = 1 and stuck_stage = 'pa_not_submitted' then 1 else 0 end) as stuck_pa_not_submitted,
    sum(case when is_stuck = 1 and stuck_stage = 'awaiting_info' then 1 else 0 end) as stuck_awaiting_info,
    sum(case when is_stuck = 1 and stuck_stage = 'awaiting_decision' then 1 else 0 end) as stuck_awaiting_decision,
    sum(case when is_stuck = 1 and stuck_stage = 'awaiting_appeal' then 1 else 0 end) as stuck_awaiting_appeal
from {{ ref('fct_pa_cases') }}
where is_qa_fixture = 0
group by 1
