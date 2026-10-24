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

## Read more

- How it works and why: `docs/ARCHITECTURE.md`
- Product thinking: `outputs/opportunity-analyses/`, `outputs/prds/`, `outputs/decisions/`
