with coalesced as (
    select
        event_id,
        case_id,
        clinic_id,
        event_type,
        event_at_raw,
        recorded_at,
        coalesce(event_at_raw, recorded_at) as event_at_coalesced,
        denial_reason_code_raw,
        denial_reason_text,
        is_correction,
        is_duplicate_event,
        is_late_arrival,
        event_at_missing,
        is_chronology_error,
        source_channel
    from {{ ref('stg_case_events') }}
),

deduped as (
    select *
    from coalesced
    qualify row_number() over (
        partition by case_id, event_type, date_trunc('minute', event_at_coalesced)
        order by recorded_at desc, event_id desc
    ) = 1
),

submitted as (
    select
        case_id,
        min(event_at_coalesced) as submitted_at
    from deduped
    where event_type = 'pa_submitted'
    group by 1
),

repaired as (
    select
        d.event_id,
        d.case_id,
        d.clinic_id,
        d.event_type,
        d.event_at_raw,
        d.recorded_at,
        case
            when d.event_type in ('decision_approved', 'decision_denied')
                and s.submitted_at is not null
                and d.event_at_coalesced < s.submitted_at
                and d.recorded_at >= s.submitted_at
                then d.recorded_at
            when d.event_type in ('decision_approved', 'decision_denied')
                and s.submitted_at is not null
                and d.event_at_coalesced < s.submitted_at
                then null
            else d.event_at_coalesced
        end as event_at,
        case
            when d.event_type in ('decision_approved', 'decision_denied')
                and s.submitted_at is not null
                and d.event_at_coalesced < s.submitted_at
                then 1
            else 0
        end as chronology_was_repaired,
        d.denial_reason_code_raw,
        d.denial_reason_text,
        d.is_correction,
        d.is_duplicate_event,
        d.is_late_arrival,
        d.event_at_missing,
        d.is_chronology_error,
        d.source_channel
    from deduped as d
    left join submitted as s
        on d.case_id = s.case_id
),

coded as (
    select
        r.*,
        coalesce(
            r.denial_reason_code_raw,
            m.denial_reason_code,
            case
                when r.denial_reason_text is not null then 'unmapped'
            end
        ) as denial_reason_code,
        case
            when r.denial_reason_code_raw is null
                and r.denial_reason_text is not null
                and m.denial_reason_code is null
                then 1
            else 0
        end as denial_text_unmapped
    from repaired as r
    left join {{ ref('stg_denial_reason_map') }} as m
        on lower(r.denial_reason_text) = m.source_text
)

select *
from coded
