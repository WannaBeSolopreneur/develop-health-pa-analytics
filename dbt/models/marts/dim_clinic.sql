select
    clinic_id,
    clinic_name,
    city,
    state,
    organization_type,
    is_featured
from {{ ref('stg_clinics') }}
