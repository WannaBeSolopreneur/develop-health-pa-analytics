-- Snowflake row access policy for this model.
-- Not executed by the DuckDB demo. The live demo enforces the same predicate
-- with fct_pa_cases_tenant and with the clinic agent's where clause.
--
-- Internal analysts are in PA_INTERNAL_ANALYTICS and can read every tenant.
-- A clinic or pharmacy role is listed in governance.role_account_access
-- for exactly one clinic_id. A pharmacy is a tenant. The key is clinic_id
-- because that is the account on the fact.

create schema if not exists governance;

create or replace table governance.role_account_access (
    role_name varchar,
    clinic_id varchar
);

-- Example grants. Load these from the application, do not hard-code customers.
-- insert into governance.role_account_access values
--     ('LAKESHORE_ANALYST', 'clinic_lakeshore'),
--     ('RIVER_CITY_PHARMACY', 'pharmacy_river');

create or replace row access policy governance.pa_clinic_isolation
as (clinic_id varchar) returns boolean ->
    is_role_in_session('PA_INTERNAL_ANALYTICS')
    or exists (
        select 1
        from governance.role_account_access as access
        where access.role_name = current_role()
          and access.clinic_id = clinic_id
    );

-- Apply the policy to every customer-facing table that carries clinic_id.
-- alter table marts.fct_pa_cases
--   add row access policy governance.pa_clinic_isolation on (clinic_id);
-- alter table marts.mart_headline_metrics
--   add row access policy governance.pa_clinic_isolation on (clinic_id);
-- alter table marts.mart_pa_funnel
--   add row access policy governance.pa_clinic_isolation on (clinic_id);
-- alter table marts.mart_cases_stuck
--   add row access policy governance.pa_clinic_isolation on (clinic_id);

-- The clinic agent must also put the predicate in the SQL it shows,
-- so a person can see why a row is in or out. The policy is the backstop.
-- Do not give the agent a role that bypasses the policy.
