PY ?= python

.PHONY: install lint test claims rules-doc cedar check demo eval evidence serve doctor clean

install:
	$(PY) -m pip install -e ".[dev,api,cedar]"

lint:
	$(PY) -m ruff check src tests scripts
	$(PY) -m ruff format --check src tests scripts

test:
	$(PY) -m pytest

claims:
	$(PY) scripts/lint_claims.py

rules-doc:
	$(PY) scripts/gen_rules_doc.py

cedar:
	$(PY) scripts/export_cedar.py

check: lint test claims

demo:
	mandat demo

eval:
	mandat eval --per-defect 6

evidence:
	$(PY) scripts/build_evidence.py

serve:
	mandat serve

doctor:
	$(PY) scripts/doctor.py

clean:
	rm -rf var build dist .pytest_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
