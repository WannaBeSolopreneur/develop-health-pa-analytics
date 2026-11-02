{% macro hours_between(start_ts, end_ts) -%}
{%- if target.type == 'snowflake' -%}
datediff('second', {{ start_ts }}, {{ end_ts }}) / 3600.0
{%- else -%}
date_diff('second', {{ start_ts }}, {{ end_ts }}) / 3600.0
{%- endif -%}
{%- endmacro %}
