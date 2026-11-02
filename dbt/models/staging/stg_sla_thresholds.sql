select
    line_of_business,
    stage,
    priority,
    cast(sla_hours as integer) as sla_hours,
    basis
from {{ ref('sla_thresholds') }}
