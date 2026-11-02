select
    lower(trim(source_text)) as source_text,
    denial_reason_code
from {{ ref('denial_reason_map') }}
