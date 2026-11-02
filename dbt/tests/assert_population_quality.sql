select patient_id
from {{ ref('dim_patient') }}
where birth_date > date '2006-12-31'
    or birth_date < date '1946-01-01'

union all

select prescriber_id
from {{ ref('dim_prescriber') }}
where npi_is_synthetic <> 1
    or length(npi) <> 10

union all

select 'mess'
from {{ ref('mart_data_quality') }}
where duplicate_events < 100
    or late_events < 100
    or decision_corrections < 50
    or duplicate_case_rows < 50
    or dirty_diagnoses < 20
    or unmapped_denial_text < 10

union all

select c.case_id
from {{ ref('stg_cases') }} as c
left join {{ ref('fct_pa_cases') }} as f
    on c.case_id = f.case_id
where f.case_id is null
