select
    patient_id,
    clinic_id,
    first_name,
    last_name,
    cast(birth_date as date) as birth_date,
    member_id,
    cast(is_duplicate_row as integer) as is_duplicate_row
from {{ source('raw', 'patients') }}
qualify row_number() over (
    partition by patient_id
    order by try_cast(updated_at as timestamp) desc
) = 1
