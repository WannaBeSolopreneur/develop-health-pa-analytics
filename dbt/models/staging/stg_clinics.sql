select
    clinic_id,
    clinic_name,
    city,
    state,
    organization_type,
    cast(is_featured as integer) as is_featured
from {{ source('raw', 'clinics') }}
