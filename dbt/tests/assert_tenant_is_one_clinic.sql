select clinic_id
from {{ ref('fct_pa_cases_tenant') }}
where clinic_id <> '{{ var("demo_clinic_id") }}'
    or is_qa_fixture <> 0

union all

select 'too_small'
from {{ ref('fct_pa_cases_tenant') }}
having count(*) < 3000

union all

select 'too_few_clinics_in_population'
from {{ ref('fct_pa_cases') }}
where is_qa_fixture = 0
having count(*) < 20000
