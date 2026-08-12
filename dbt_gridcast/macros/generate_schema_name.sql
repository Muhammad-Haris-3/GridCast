{#
    Use the configured schema verbatim rather than dbt's default of prefixing
    it with the target schema.

    Without this, a model configured as `staging` lands in `marts_staging`,
    which would make the SQL files, the DDL in sql/, and the grants in
    004_roles.sql all refer to schemas that do not exist.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
