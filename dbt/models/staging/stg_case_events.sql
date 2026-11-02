select
    event_id,
    case_id,
    clinic_id,
    lower(trim(event_type)) as event_type,
    try_cast(nullif(event_at, '') as timestamp) as event_at_raw,
    try_cast(recorded_at as timestamp) as recorded_at,
    nullif(trim(denial_reason_code), '') as denial_reason_code_raw,
    nullif(trim(denial_reason_text), '') as denial_reason_text,
    cast(is_correction as integer) as is_correction,
    cast(is_duplicate_event as integer) as is_duplicate_event,
    cast(is_late_arrival as integer) as is_late_arrival,
    cast(event_at_missing as integer) as event_at_missing,
    cast(is_chronology_error as integer) as is_chronology_error,
    lower(trim(source_channel)) as source_channel
from {{ source('raw', 'case_events') }}
