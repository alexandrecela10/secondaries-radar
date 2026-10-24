# First Call. One command per job. Python 3.11 via uv.
PY := .venv/bin/python
UV := $(HOME)/.local/bin/uv
FC := $(PY) -m fc.cli

.PHONY: setup up collect-batch collect-stream demo weekly test eval cases measure down clean

setup:            ## create the venv and install dependencies
	$(UV) venv --python 3.11 --allow-existing
	$(UV) sync

up:               ## start Redpanda (Kafka API on localhost:19092)
	docker compose up -d
	@for i in $$(seq 1 30); do docker exec firstcall-redpanda rpk cluster health 2>/dev/null | grep -q 'Healthy:.*true' && break; sleep 2; done
	@docker exec firstcall-redpanda rpk cluster health | head -3

collect-batch:    ## batch path: fetch all sources once, classify, score
	$(FC) collect

collect-stream:   ## stream path: poll once, publish, consume until idle (Redpanda if up, else file log)
	$(FC) poll --once
	$(FC) consume --idle-exit 5

demo: collect-batch   ## live collect, then the weekly list, HTML page and one evidence view
	$(FC) weekly --html
	@echo
	$(FC) why nyc_funds

weekly:
	$(FC) weekly --html

test:             ## all evals and unit tests, offline
	$(PY) -m pytest -q

eval:             ## classifier precision and recall on the labelled headlines
	$(FC) eval --errors

cases:            ## replay the two worked historical cases
	$(FC) cases

measure:          ## run the batch vs stream experiment, write measurements/*.json
	$(FC) measure

down:             ## stop Redpanda
	docker compose down

clean:
	rm -rf data/*.duckdb data/stream_log data/poller_seen.json reports
