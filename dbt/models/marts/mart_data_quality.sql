select
    (
        select count(*)
        from {{ ref('stg_case_events') }} as e
        inner join {{ ref('stg_cases') }} as c
            on e.case_id = c.case_id
        where e.is_duplicate_event = 1
            and c.is_qa_fixture = 0
    ) as duplicate_events,
    (
        select count(*)
        from {{ ref('stg_case_events') }} as e
        inner join {{ ref('stg_cases') }} as c
            on e.case_id = c.case_id
        where e.is_late_arrival = 1
            and c.is_qa_fixture = 0
    ) as late_events,
    (
        select count(*)
        from {{ ref('stg_case_events') }} as e
        inner join {{ ref('stg_cases') }} as c
            on e.case_id = c.case_id
        where e.is_correction = 1
            and c.is_qa_fixture = 0
    ) as decision_corrections,
    (
        select count(*)
        from {{ ref('stg_case_events') }} as e
        inner join {{ ref('stg_cases') }} as c
            on e.case_id = c.case_id
        where e.event_at_missing = 1
            and c.is_qa_fixture = 0
    ) as missing_event_timestamps,
    (
        select count(*)
        from {{ ref('stg_case_events') }} as e
        inner join {{ ref('stg_cases') }} as c
            on e.case_id = c.case_id
        where e.is_chronology_error = 1
            and c.is_qa_fixture = 0
    ) as chronology_errors,
    (
        select count(*)
        from {{ ref('int_events_resolved') }} as e
        inner join {{ ref('stg_cases') }} as c
            on e.case_id = c.case_id
        where e.denial_text_unmapped = 1
            and c.is_qa_fixture = 0
    ) as unmapped_denial_text,
    (
        select count(*)
        from {{ source('raw', 'cases') }} as raw_cases
        where raw_cases.is_duplicate_row = '1'
            and raw_cases.is_qa_fixture = '0'
    ) as duplicate_case_rows,
    (
        select count(*)
        from {{ ref('stg_cases') }} as clean_cases
        where clean_cases.diagnosis_is_dirty = 1
            and clean_cases.is_qa_fixture = 0
    ) as dirty_diagnoses
