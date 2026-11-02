select clinic_id
from {{ ref('mart_pa_funnel') }}
where cases_approved > cases_decided_after_submit
    or cases_decided_after_submit > cases_submitted
    or cases_submitted > cases_pa_required
    or cases_pa_required > cases_benefit_verified
    or cases_benefit_verified > cases_started
