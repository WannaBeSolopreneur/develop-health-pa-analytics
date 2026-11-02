-- Customer-facing slice. In Snowflake this predicate is a row access policy.
-- The demo clinic is set with var demo_clinic_id. QA fixtures stay off this table.
select *
from {{ ref('fct_pa_cases') }}
where clinic_id = '{{ var("demo_clinic_id") }}'
    and is_qa_fixture = 0
