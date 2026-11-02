select
    payer_id,
    payer_name,
    line_of_business,
    is_fictional_plan
from {{ ref('stg_payers') }}
