select
    patient_id,
    clinic_id,
    first_name,
    last_name,
    birth_date,
    member_id
from {{ ref('stg_patients') }}
