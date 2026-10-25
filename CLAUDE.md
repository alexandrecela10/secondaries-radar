# CLAUDE: secondaries-screening

**Product:** a tool that screens secondaries opportunities proactively and lets the user study any one of them in depth through chat.

## PM OS

This repo runs on PM OS.

- **`{pm-os}`** = `/Users/alexandrecela/Documents/PM-OS-fresh`. Shared and read-only: `skills/`, `templates/`, `frameworks/`, `voice/`, `agents/`.
- **Follow every rule in `{pm-os}/CLAUDE.md`** (imported: @/Users/alexandrecela/Documents/PM-OS-fresh/CLAUDE.md). That covers voice, factual tone, atomic problems, goals and state files, the Value Equation, decision storage and the PRD lifecycle. The rules there take precedence over defaults.
- **Working repo** = this repo. Read context from `context-library/`. Write every new PM document to `outputs/`. Never write to `context-library/`. The PM moves finished work there.
- Skills are linked at `.claude/skills` and point to `{pm-os}/skills`.

## Brief

**Read `context-library/strategy/secondaries-radar-brief.md` at the start of every session.** It holds scope, the semantic layer, the metrics, milestones and the definition of done. The PM wants it challenged where it's weak.

- **Working style:** senior pair. Propose a plan with milestones, wait for approval, then build step by step. Surgical edits. Brief reasoning.
- **Two goals with equal weight:** (A) the prototype, (B) teaching the PM LP-led secondaries. Explain each domain concept with a worked example and how a buyer thinks about it. Quiz 3 to 5 questions now and then. Flag simplifications a real investor would reject.
- **Hard constraint (amended 2026-10-24, PM approved):** no employer data, ever. The buyer's own portfolio, holdings, bids and teasers are synthetic, with invented names. Gate 0 (First Call) may use public data on the seller side: pension disclosures, board materials, news headlines and links. Show every seller signal as a quoted, linked source, never as a verdict. Never state a fabricated fact about a real organisation (for example, a synthetic holding shared with a real pension). Store headline, link and a short quote, not full articles. Real pension names and article links may be shown in First Call (amended 2026-10-25, PM approved): they are public news. Run `/name-audit` before publishing anything else.
- **Names (2026-10-25, PM approved):** First Call is shown as **Seller Watch** and Quick Look as **Offer Check** in the app and docs. Code folders keep `firstcall/` and `quicklook/`.
- **Scope (2026-10-24):** Two gates in scope: First Call (Gate 0, spot likely sellers) and Quick Look (Gate 1, assess a sale offer in under an hour). Audience priority: a data leader over an engineering lead. Demo-grade working prototype, not production.
- **Learning spike:** build a streaming ingest path next to the batch path to teach the PM streaming. Measure the trade-offs, then ship batch. Plan in `outputs/analyses/first-call-batch-vs-stream-plan.md`.

## Layout

- `context-library/`: business info, stakeholders, research, decisions, strategy for this product.
- `outputs/`: PM OS drafts (opportunity analysis, PRD, decisions, portfolio case study).
- `docs/`: product docs that ship with the code (`GLOSSARY.md`, `INVESTOR_NOTES.md`, `ARCHITECTURE.md`, `CALIBRATION.md`). Claude maintains these directly.
- Prototype code: repo root. Python 3.11+, DuckDB, Pydantic, pytest.

## Workflow

`/opportunity-analysis` → `/prd-draft` → `/napkin-sketch` → `/prototype` → `/feature-results` → `/name-audit` → `/portfolio-case-study`
