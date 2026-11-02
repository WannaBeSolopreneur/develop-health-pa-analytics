select
    drug_id,
    brand_name,
    generic_name,
    rxcui,
    product_ndc,
    package_ndc,
    labeler_name,
    dosage_form,
    strength_description,
    drug_class,
    bridge_eligible
from {{ ref('stg_drugs') }}
