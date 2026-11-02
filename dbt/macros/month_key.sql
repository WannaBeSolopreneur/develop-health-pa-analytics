{% macro month_key(ts) -%}
{%- if target.type == 'snowflake' -%}
to_varchar({{ ts }}, 'YYYY-MM')
{%- else -%}
strftime({{ ts }}, '%Y-%m')
{%- endif -%}
{%- endmacro %}
