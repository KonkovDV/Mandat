PY ?= python

.PHONY: install lint test claims rules-doc cedar check demo eval evidence serve doctor clean deck demo-cards

install:
	$(PY) -m pip install --require-hashes -r requirements/pip.lock
	$(PY) -m pip install --require-hashes -r requirements/dev.lock -r requirements/build.lock
	$(PY) -m pip install --no-deps --no-build-isolation -e .

lint:
	$(PY) -m ruff check src tests scripts tools
	$(PY) -m ruff format --check src tests scripts tools
	$(PY) -m mypy

test:
	$(PY) -m pytest

claims:
	$(PY) scripts/lint_claims.py
	$(PY) scripts/check_links.py

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

demo-cards: demo
	$(PY) scripts/export_demo_cards.py

deck:
	cd submission/deck && npm ci && node build_deck.js ../../evidence.json ../slides/Mandat_VTB.pptx
	$(PY) submission/deck/finish_deck.py submission/slides/Mandat_VTB.pptx
	soffice --headless --convert-to pdf --outdir submission/slides submission/slides/Mandat_VTB.pptx

clean:
	rm -rf var build dist .pytest_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
