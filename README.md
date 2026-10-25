# Secondaries Radar

A working prototype for a fund that buys private equity stakes from investors who want out early.

- **Quick Look** (`quicklook/`): reads a broker's sale offer, identifies each fund from its numbers (names are hidden before an NDA), and gives a sourced verdict: look closer or pass. Persona and journey: `outputs/journey-maps/quick-look-associate-journey.md`.
- **First Call** (`firstcall/`): reads real public news and lists pensions showing signs they may sell, with quotes and links. Persona and journey: `outputs/journey-maps/first-call-partner-journey.md`.

Our own portfolio and offers are synthetic, with invented names. First Call uses public news headlines and short quotes only. Not investment advice, and not a statement of any pension's intent.

## Run it

```
cd quicklook && make setup && make journey
cd firstcall && make setup && make demo
```

Optional keys go in `.env` at the repo root: `ANTHROPIC_API_KEY` (model parsing and classification), and `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` (tracing and experiments). Without keys, both features run on offline parsers and tracing is switched off.

## Demo

A guided Streamlit demo, written to you: three tabs, one per persona plus "Under the hood". It needs no key and costs nothing.

```
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/streamlit run app.py
.venv/bin/pytest tests        # smoke test: data behind each tab, then the app headless
```

- **Quick Look** runs live: the offline parser reads the offer (or text you paste), and matching, metrics and verdicts are the production code. The verdict answers two questions: does it fit us, and is the price right by what our own fund reports told us since the broker's date (try Cedar, then Hornbeam). Change the rules and the verdict recomputes; the holdout table shows why rule changes are tested.
- **First Call** replays a committed snapshot of one real news run (`demo/data/firstcall_snapshot.json`), with real pension names, headlines, short quotes and article links. All of it is public news; we never store the article itself.
- **Snapshot:** `python demo/build_snapshot.py [--firstcall-db firstcall/data/firstcall.duckdb]` rebuilds `demo/data/` (the synthetic universe takes about 10 seconds to generate, so the app reads a copy).

**Deploy on Streamlit Community Cloud:** point a new app at this repo, branch `main`, main file `app.py`, and pick Python 3.11 under Advanced settings. `requirements.txt` installs Streamlit and the two local packages (`./quicklook`, `./firstcall`). Optional live mode: add `ANTHROPIC_API_KEY` under the app's Secrets. Claude can then read pasted offers and headlines, capped at 3 calls per visitor and 30 per day. Without the secret, live mode is hidden. Never commit `.streamlit/secrets.toml`.

## Read more

- How it works and why: `docs/ARCHITECTURE.md`
- Product thinking: `outputs/opportunity-analyses/`, `outputs/prds/`, `outputs/decisions/`
