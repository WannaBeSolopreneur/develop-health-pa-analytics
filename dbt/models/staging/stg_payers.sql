select
    payer_id,
    payer_name,
    line_of_business,
    cast(is_fictional_plan as integer) as is_fictional_plan
from {{ source('raw', 'payers') }}
