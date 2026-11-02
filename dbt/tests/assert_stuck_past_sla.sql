select case_id
from {{ ref('fct_pa_cases') }}
where is_stuck = 1
    and (
        is_open <> 1
        or hours_in_stage <= sla_hours
        or sla_hours is null
        or stuck_stage is null
    )

union all

select case_id
from {{ ref('fct_pa_cases') }}
where is_open = 1
    and current_stage <> 'closed'
    and sla_hours is null
