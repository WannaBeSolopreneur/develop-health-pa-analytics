{% macro repair_icd(expr) -%}
case
    when {{ expr }} is null or trim({{ expr }}) = '' then null
    when position('.' in upper(trim({{ expr }}))) > 0 then upper(trim({{ expr }}))
    when length(replace(upper(trim({{ expr }})), '.', '')) >= 4
        then substr(replace(upper(trim({{ expr }})), '.', ''), 1, 3)
            || '.'
            || substr(replace(upper(trim({{ expr }})), '.', ''), 4)
    else upper(trim({{ expr }}))
end
{%- endmacro %}
