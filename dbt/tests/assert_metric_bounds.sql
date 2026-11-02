select clinic_id, 'approval_rate' as metric_name
from {{ ref('mart_headline_metrics') }}
where approval_rate < 0
    or approval_rate > 1
    or approval_numerator > approval_denominator
    or first_pass_approval_rate < 0
    or first_pass_approval_rate > 1
    or first_pass_numerator > first_pass_denominator
    or abandonment_rate < 0
    or abandonment_rate > 1
    or abandon_numerator > abandon_denominator
    or appeal_overturn_rate < 0
    or appeal_overturn_rate > 1
    or appeal_numerator > appeal_denominator
    or median_hours_to_decision < 0
    or median_hours_to_therapy < 0
    or cases_stuck < 0
