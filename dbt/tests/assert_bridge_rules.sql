select case_id
from {{ ref('fct_pa_cases') }}
where coverage_path = 'glp1_bridge'
    and (
        payer_id <> 'payer_glp1_bridge'
        or initiated_at < cast('{{ var("bridge_start_timestamp") }}' as timestamp)
        or drug_id not in ('drug_wegovy', 'drug_zepbound_kwikpen', 'drug_foundayo')
        or diagnosis_code like 'E11%'
        or diagnosis_code in ('G47.33', 'K75.81')
        or diagnosis_code like 'I21%'
        or diagnosis_code like 'I25%'
        or diagnosis_code like 'I63%'
        or diagnosis_code like 'I70%'
        or package_ndc not in (
            '0169-4524-14',
            '0002-3555-11',
            '0002-4794-31'
        )
    )

union all

select case_id
from {{ ref('fct_pa_cases') }}
where drug_id = 'drug_zepbound_pen'
    and coverage_path = 'glp1_bridge'

union all

select 'bridge_volume'
from {{ ref('fct_pa_cases_tenant') }}
where coverage_path = 'glp1_bridge'
having count(*) < 20

union all

select 'exclusion_volume'
from {{ ref('fct_pa_cases_tenant') }}
where coverage_path = 'part_d_exclusion'
having count(*) < 20
