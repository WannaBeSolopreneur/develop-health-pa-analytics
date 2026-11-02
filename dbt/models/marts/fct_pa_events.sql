select
    e.event_id,
    e.case_id,
    e.clinic_id,
    e.event_type,
    e.event_at_raw,
    e.event_at,
    e.recorded_at,
    e.denial_reason_code,
    e.denial_reason_text,
    e.denial_text_unmapped,
    e.is_correction,
    e.is_duplicate_event,
    e.is_late_arrival,
    e.event_at_missing,
    e.is_chronology_error,
    e.chronology_was_repaired,
    e.source_channel,
    c.is_qa_fixture
from {{ ref('int_events_resolved') }} as e
inner join {{ ref('stg_cases') }} as c
    on e.case_id = c.case_id
