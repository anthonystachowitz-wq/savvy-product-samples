# Transition Copilot work sample.
# All commands run from the repository root.

PYTHON ?= python3
HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: help run reseed reset eval test

help:
	@echo "make run      Start the app on http://$(HOST):$(PORT)"
	@echo "make reseed   Rebuild app.db from data/book.json and the documents"
	@echo "make reset    Delete app.db, then reseed on first request"
	@echo "make eval     Run scripts/run_eval.py against the seeded labels"
	@echo "make test     Run the pipeline test suite"

run:
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

reseed:
	$(PYTHON) -c "from app import store; store.reset_demo(); print('app.db reseeded from data/')"

reset:
	rm -f app.db
	@echo "app.db removed. The next request seeds a clean demo state."

eval:
	$(PYTHON) scripts/run_eval.py

test:
	$(PYTHON) -m pytest -q
