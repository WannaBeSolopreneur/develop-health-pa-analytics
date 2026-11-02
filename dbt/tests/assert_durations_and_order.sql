select case_id
from {{ ref('fct_pa_cases') }}
where hours_to_decision < 0
    or hours_to_therapy < 0
    or (decision_at is not null and submitted_at is not null and decision_at < submitted_at)
    or (final_outcome = 'approved' and submitted_at is null)
