# Quick Look. Every target is one command. Runs offline unless ANTHROPIC_API_KEY is set.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
RADAR = .venv/bin/radar

.PHONY: setup generate demo test eval measure serve batch send clean

setup:            ## create the venv (Python 3.11) and install
	$(UV) venv --python 3.11 --allow-existing
	$(UV) sync

generate: setup   ## synthetic universe, offers (.eml) and labels, seed 42
	$(RADAR) generate

demo: generate    ## ingest every offer, print Cedar's verdict and brief path
	rm -rf data/main
	$(RADAR) demo

test: setup       ## all tests, offline
	.venv/bin/pytest

eval: setup       ## the evals only (C-3), then the numbers
	.venv/bin/pytest -k "eval" -v
	@.venv/bin/python -c "import json; d=json.load(open('measurements/evals.json')); [print(f'{k}: {v}') for k, v in d.items()]"

measure: generate ## collection architecture experiment, raw results in measurements/
	$(RADAR) measure

serve:            ## real-time path: SMTP on 127.0.0.1:2525
	$(RADAR) --workspace realtime serve

batch:            ## batch path: scan outbox/maildir every 15 minutes
	$(RADAR) --workspace batch batch --interval 900

send:             ## send all offers to the SMTP server
	$(RADAR) send --to smtp

clean:
	rm -rf data outbox
