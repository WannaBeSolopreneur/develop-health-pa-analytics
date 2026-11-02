with events as (
    select *
    from {{ ref('int_events_resolved') }}
    where event_at is not null
),

effective_decision as (
    select
        case_id,
        event_at as decision_at,
        case
            when event_type = 'decision_approved' then 'approved'
            else 'denied'
        end as decision_outcome,
        denial_reason_code as decision_denial_reason,
        is_correction as decision_was_correction
    from events
    where event_type in ('decision_approved', 'decision_denied')
    qualify row_number() over (
        partition by case_id
        order by recorded_at desc, event_id desc
    ) = 1
),

effective_appeal as (
    select
        case_id,
        event_at as appeal_decision_at,
        case
            when event_type = 'appeal_approved' then 'approved'
            else 'denied'
        end as appeal_outcome
    from events
    where event_type in ('appeal_approved', 'appeal_denied')
    qualify row_number() over (
        partition by case_id
        order by recorded_at desc, event_id desc
    ) = 1
),

times as (
    select
        case_id,
        min(case when event_type = 'benefit_check_started' then event_at end) as benefit_check_started_at,
        min(case when event_type = 'benefit_verified' then event_at end) as benefit_verified_at,
        min(case when event_type = 'pa_required' then event_at end) as pa_required_at,
        min(case when event_type = 'pa_not_required' then event_at end) as pa_not_required_at,
        min(case when event_type = 'pa_submitted' then event_at end) as submitted_at,
        min(case when event_type = 'info_requested' then event_at end) as info_requested_at,
        min(case when event_type = 'info_received' then event_at end) as info_received_at,
        min(case when event_type = 'abandoned' then event_at end) as abandoned_at,
        min(case when event_type = 'appeal_submitted' then event_at end) as appeal_submitted_at,
        min(case when event_type = 'bridge_claim_denied' then event_at end) as bridge_claim_denied_at,
        min(case when event_type = 'plan_redirect_noted' then event_at end) as plan_redirect_noted_at
    from events
    group by 1
),

joined as (
    select
        c.case_id,
        c.clinic_id,
        cl.clinic_name,
        cl.city as clinic_city,
        cl.state as clinic_state,
        cl.organization_type,
        c.patient_id,
        c.prescriber_id,
        c.payer_id,
        p.payer_name,
        p.line_of_business,
        c.source_payer_id,
        sp.payer_name as source_payer_name,
        c.drug_id,
        d.brand_name as drug_name,
        d.generic_name,
        d.drug_class,
        d.package_ndc,
        d.bridge_eligible,
        c.diagnosis_code,
        c.diagnosis_code_raw,
        c.secondary_diagnosis_code,
        c.channel,
        c.priority,
        c.coverage_path,
        c.indication_match,
        c.is_reauthorization,
        c.bmi,
        c.lifestyle_attested,
        c.initiated_at,
        c.is_qa_fixture,
        c.diagnosis_is_dirty,
        t.benefit_check_started_at,
        t.benefit_verified_at,
        t.pa_required_at,
        t.pa_not_required_at,
        t.submitted_at,
        t.info_requested_at,
        t.info_received_at,
        t.abandoned_at,
        t.appeal_submitted_at,
        t.bridge_claim_denied_at,
        t.plan_redirect_noted_at,
        ed.decision_at,
        ed.decision_outcome,
        ed.decision_denial_reason,
        ed.decision_was_correction,
        ea.appeal_decision_at,
        ea.appeal_outcome
    from {{ ref('stg_cases') }} as c
    inner join {{ ref('stg_clinics') }} as cl
        on c.clinic_id = cl.clinic_id
    inner join {{ ref('stg_payers') }} as p
        on c.payer_id = p.payer_id
    inner join {{ ref('stg_payers') }} as sp
        on c.source_payer_id = sp.payer_id
    inner join {{ ref('stg_drugs') }} as d
        on c.drug_id = d.drug_id
    left join times as t
        on c.case_id = t.case_id
    left join effective_decision as ed
        on c.case_id = ed.case_id
    left join effective_appeal as ea
        on c.case_id = ea.case_id
),

outcomes as (
    select
        *,
        case
            when appeal_submitted_at is not null and appeal_decision_at is null then 'pending'
            when appeal_outcome = 'approved' then 'approved'
            when appeal_outcome = 'denied' then 'denied'
            when decision_outcome = 'approved' then 'approved'
            when decision_outcome = 'denied' then 'denied'
            when abandoned_at is not null then 'abandoned'
            when pa_not_required_at is not null then 'covered_no_pa'
            else 'pending'
        end as final_outcome,
        case
            when appeal_outcome = 'approved' then appeal_decision_at
            when decision_outcome = 'approved' and appeal_decision_at is null then decision_at
        end as approval_at
    from joined
),

staged as (
    select
        *,
        case
            when final_outcome <> 'pending' then 'closed'
            when benefit_verified_at is null then 'benefit_check'
            when pa_required_at is not null
                and submitted_at is null
                and decision_at is null
                then 'pa_not_submitted'
            when info_requested_at is not null
                and info_received_at is null
                and decision_at is null
                then 'awaiting_info'
            when appeal_submitted_at is not null and appeal_decision_at is null then 'awaiting_appeal'
            when submitted_at is not null and decision_at is null then 'awaiting_decision'
            else 'benefit_check'
        end as current_stage
    from outcomes
),

with_clock as (
    select
        s.*,
        case s.current_stage
            when 'benefit_check' then coalesce(s.benefit_verified_at, s.benefit_check_started_at)
            when 'pa_not_submitted' then s.pa_required_at
            when 'awaiting_info' then s.info_requested_at
            when 'awaiting_decision' then coalesce(s.info_received_at, s.submitted_at)
            when 'awaiting_appeal' then s.appeal_submitted_at
        end as stage_started_at,
        sla.sla_hours,
        sla.basis as sla_basis
    from staged as s
    left join {{ ref('stg_sla_thresholds') }} as sla
        on s.line_of_business = sla.line_of_business
        and s.current_stage = sla.stage
        and s.priority = sla.priority
),

measured as (
    select
        *,
        case
            when decision_at is not null and submitted_at is not null
                then {{ hours_between('submitted_at', 'decision_at') }}
        end as hours_to_decision,
        case
            when final_outcome = 'approved' and approval_at is not null and benefit_check_started_at is not null
                then {{ hours_between('benefit_check_started_at', 'approval_at') }}
        end as hours_to_therapy,
        case
            when current_stage <> 'closed' and stage_started_at is not null
                then {{ hours_between('stage_started_at', "cast('" ~ var('as_of_timestamp') ~ "' as timestamp)") }}
        end as hours_in_stage,
        {{ month_key('initiated_at') }} as case_month
    from with_clock
)

select
    case_id,
    clinic_id,
    clinic_name,
    clinic_city,
    clinic_state,
    organization_type,
    patient_id,
    prescriber_id,
    payer_id,
    payer_name,
    line_of_business,
    source_payer_id,
    source_payer_name,
    drug_id,
    drug_name,
    generic_name,
    drug_class,
    package_ndc,
    bridge_eligible,
    diagnosis_code,
    diagnosis_code_raw,
    secondary_diagnosis_code,
    channel,
    priority,
    coverage_path,
    indication_match,
    is_reauthorization,
    bmi,
    lifestyle_attested,
    initiated_at,
    is_qa_fixture,
    diagnosis_is_dirty,
    benefit_check_started_at,
    benefit_verified_at,
    pa_required_at,
    pa_not_required_at,
    submitted_at,
    info_requested_at,
    info_received_at,
    abandoned_at,
    appeal_submitted_at,
    bridge_claim_denied_at,
    plan_redirect_noted_at,
    decision_at,
    decision_outcome,
    decision_denial_reason,
    decision_was_correction,
    appeal_decision_at,
    appeal_outcome,
    final_outcome,
    approval_at,
    current_stage,
    stage_started_at,
    sla_hours,
    sla_basis,
    hours_to_decision,
    hours_to_therapy,
    hours_in_stage,
    case_month,
    case when final_outcome = 'pending' then 1 else 0 end as is_open,
    case
        when final_outcome = 'pending'
            and sla_hours is not null
            and hours_in_stage > sla_hours
            then 1
        else 0
    end as is_stuck,
    case
        when final_outcome = 'pending'
            and sla_hours is not null
            and hours_in_stage > sla_hours
            then current_stage
    end as stuck_stage,
    case when final_outcome in ('approved', 'denied') then 1 else 0 end as is_decision_denominator,
    case when final_outcome = 'approved' then 1 else 0 end as is_final_approved,
    case when final_outcome in ('approved', 'denied') then 1 else 0 end as is_first_pass_denominator,
    case
        when final_outcome = 'approved'
            and decision_outcome = 'approved'
            and info_requested_at is null
            and appeal_submitted_at is null
            then 1
        else 0
    end as is_first_pass_approved,
    case when final_outcome = 'abandoned' then 1 else 0 end as is_abandoned,
    case
        when pa_required_at is not null
            and final_outcome in ('abandoned', 'approved', 'denied')
            then 1
        else 0
    end as is_abandon_denominator,
    case when appeal_decision_at is not null then 1 else 0 end as is_appeal_denominator,
    case when appeal_outcome = 'approved' then 1 else 0 end as is_appeal_overturned,
    case when benefit_check_started_at is not null then 1 else 0 end as reached_benefit_check,
    case when benefit_verified_at is not null then 1 else 0 end as reached_benefit_verified,
    case when pa_required_at is not null then 1 else 0 end as reached_pa_required,
    case when submitted_at is not null then 1 else 0 end as reached_submitted,
    case when info_requested_at is not null then 1 else 0 end as reached_info_requested,
    case when decision_at is not null then 1 else 0 end as reached_decision,
    case when decision_at is not null and submitted_at is not null then 1 else 0 end as reached_decision_after_submit
from measured
