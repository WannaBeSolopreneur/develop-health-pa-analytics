select
    prescriber_id,
    clinic_id,
    npi,
    first_name,
    last_name,
    specialty,
    npi_is_synthetic
from {{ ref('stg_prescribers') }}
