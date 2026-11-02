-- Hand-built raw cases. Each select returns a row only when the clean model is wrong.

select 'qa_correction' as case_id, final_outcome
from {{ ref('fct_pa_cases') }}
where case_id = 'qa_correction'
    and final_outcome <> 'denied'

union all

select 'qa_late', cast(hours_to_decision as varchar)
from {{ ref('fct_pa_cases') }}
where case_id = 'qa_late'
    and abs(hours_to_decision - 12) > 0.01

union all

select 'qa_chrono', cast(decision_at as varchar)
from {{ ref('fct_pa_cases') }}
where case_id = 'qa_chrono'
    and decision_at <> timestamp '2026-01-16 15:00:00'

union all

select 'qa_dup_case', channel
from {{ ref('fct_pa_cases') }}
where case_id = 'qa_dup_case'
    and channel <> 'fax'

union all

select 'qa_dirty_dx', diagnosis_code
from {{ ref('fct_pa_cases') }}
where case_id = 'qa_dirty_dx'
    and diagnosis_code <> 'E11.9'

union all

select 'qa_dup_event', cast(count(*) as varchar)
from {{ ref('fct_pa_events') }}
where case_id = 'qa_dup_event'
    and event_type = 'benefit_check_started'
having count(*) <> 1
