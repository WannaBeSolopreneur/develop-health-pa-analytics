with typed as (
    select
        case_id,
        clinic_id,
        patient_id,
        prescriber_id,
        payer_id,
        source_payer_id,
        drug_id,
        diagnosis_code as diagnosis_code_raw,
        {{ repair_icd('diagnosis_code') }} as diagnosis_code,
        secondary_diagnosis_code as secondary_diagnosis_code_raw,
        {{ repair_icd('secondary_diagnosis_code') }} as secondary_diagnosis_code,
        lower(trim(channel)) as channel,
        lower(trim(priority)) as priority,
        coverage_path,
        indication_match,
        cast(is_reauthorization as integer) as is_reauthorization,
        try_cast(nullif(bmi, '') as double) as bmi,
        cast(lifestyle_attested as integer) as lifestyle_attested,
        cast(uncontrolled_hypertension as integer) as uncontrolled_hypertension,
        try_cast(initiated_at as timestamp) as initiated_at,
        try_cast(updated_at as timestamp) as updated_at,
        try_cast(recorded_at as timestamp) as recorded_at,
        cast(is_duplicate_row as integer) as is_duplicate_row,
        cast(is_qa_fixture as integer) as is_qa_fixture,
        cast(diagnosis_is_dirty as integer) as diagnosis_is_dirty
    from {{ source('raw', 'cases') }}
)

select *
from typed
qualify row_number() over (
    partition by case_id
    order by updated_at desc, recorded_at desc
) = 1
