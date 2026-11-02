select
    prescriber_id,
    clinic_id,
    npi,
    first_name,
    last_name,
    specialty,
    cast(npi_is_synthetic as integer) as npi_is_synthetic
from {{ source('raw', 'prescribers') }}
