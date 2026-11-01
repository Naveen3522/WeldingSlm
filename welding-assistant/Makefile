PY ?= python
export PYTHONPATH := src

run:            ## the web page + API (local mode)
	$(PY) apps/api/server.py
services:       ## understanding + answering as separate processes (then: WELD_MODE=distributed make run)
	$(PY) -m welding_ai.services.understanding --port 8801 & $(PY) -m welding_ai.services.answering --port 8802
test:
	$(PY) -m unittest discover tests -v
index:          ## rebuild passages, tables, sections from the documents
	$(PY) -m welding_ai.ingestion.ingest && $(PY) -m welding_ai.ingestion.sections
lint:
	$(PY) -m pyflakes src apps
