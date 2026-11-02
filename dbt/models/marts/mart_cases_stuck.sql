select
    clinic_id,
    stuck_stage,
    count(*) as cases_stuck
from {{ ref('fct_pa_cases') }}
where is_qa_fixture = 0
    and is_stuck = 1
group by 1, 2
