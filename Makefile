.PHONY: all venv routing generate dbt export agent page report

PYTHON := .venv/bin/python
export PA_DB_PATH := $(CURDIR)/data/pa.duckdb
export DBT_PROFILES_DIR := $(CURDIR)/dbt

all: report

.venv/bin/python: requirements.txt
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt

venv: .venv/bin/python

routing: venv
	$(PYTHON) -m pa_model.test_routing

generate: routing
	$(PYTHON) -m pa_model.generate

dbt: generate
	cd dbt && ../.venv/bin/dbt build --profiles-dir .

export: dbt
	$(PYTHON) -m pa_model.export_site

agent: export
	$(PYTHON) -m pa_model.agent.smoke

page: export
	node site/test_parser.js

report: agent page
	$(PYTHON) -m pa_model.report
