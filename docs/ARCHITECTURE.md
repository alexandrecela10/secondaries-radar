# Architecture

Two features, one pattern. **Quick Look** turns a broker's sale offer into a sourced verdict. **First Call** turns public news into a weekly list of pensions to prepare for.

## The pattern

```
source ──> bronze ──> silver ──> gold ──> verdict ──> brief / list
 (email,   (as       (typed,    (tested,   (team's     (every number
  news)    received)  checked)   versioned  rulebook)   sourced)
                                 metrics)
              model parses or classifies     code computes and decides
```

1. **The model reads; code decides.** The language model only turns text into structured fields (offer lines, signal types). Matching, metrics, scores and verdicts are plain, tested functions. This makes every verdict reproducible and auditable.
2. **Contracts between layers.** Pydantic schemas check each row. A failed row is blocked and logged, not passed on.
3. **Point in time.** A metric at date T uses only data reported on or before T. Every view shows its as-of date.
4. **Lineage on every number.** Each number in a brief carries (table, row id, metric, version). A validator fails any brief with an unsourced number.
5. **The team's rulebook.** Verdicts come from a versioned YAML file in plain English. A rule change is tested against labelled cases before it goes live, and the diff prints as sentences.
6. **Overrides are data.** Every keep, override or dismiss is stored with its reason and rulebook version, and sent to tracing as a feedback score. Over time this is the labelled set that improves the rules.
7. **Idempotent loads.** Re-ingesting the same email (Message-ID) or article (canonical link) changes nothing.

## How Quick Look identifies funds without names

Pre-NDA offers hide fund names. Inside one fund, every investor's ratios are the same at a given date: share of commitment paid in, cash returned per dollar paid in (DPI), total value per dollar paid in (TVPI). Code filters our funds by vintage, strategy and region, then scores candidates by distance on those ratios at the offer's reference date. Tolerances absorb late joiners and fee differences. Result: 96% of labelled lines matched correctly, 0 false matches under noise.

## Collection: measured, then chosen

Both features built a real-time and a batch path over the same processing code, then applied one rule: the simplest path that meets freshness, parity and recovery wins.

| | Quick Look | First Call |
|---|---|---|
| Real-time path | Local SMTP listener | Poller to Redpanda, consumer group, dead-letter topic |
| Batch path | Mailbox scan every minute | Fetch and classify every 15 minutes |
| Decision window | 1 working day | 4 weeks |
| Choice | Batch, every 1 min (2 moving parts vs 3) | Batch, every 15 min (1 moving part vs 3; 11 vs 2,160 always-on hours a month) |

Freshness is capped by the source: GDELT refreshes every 15 minutes, and pensions publish board papers days ahead. Streaming earns its place where decisions change within minutes: intraday risk, treasury cash, trade surveillance. Details: `outputs/decisions/`.

## Evals and tracing

- **Gate:** pytest evals in each feature (metric fixtures, match accuracy, classifier precision, injection cases, validator).
- **Tracing:** Langfuse, one trace per offer or batch run, with model calls as generations. It switches off silently without keys.
- **Experiments:** labelled datasets in Langfuse, run per parser or classifier (offline rules vs Claude), so a model or prompt change is compared on the same items.

## What a real deployment adds

- **Data path:** offers arrive under NDA. Use an in-region hosted model with no data retention, review the NDA and the data processing agreement, and keep offer data apart from First Call.
- **Mailbox:** Microsoft Graph delta query or IMAP search on the same one-minute schedule.
- **Storage:** DuckDB is single-writer. A shared deployment needs a server database.
- **Shared core:** the two features duplicate small pieces (lineage, layers, rulebook loader). Extract them into one package once a third user appears.

## Layout

```
quicklook/   offer intake, parsing, fingerprint matching, rulebook, briefs, evals
firstcall/   news collection (batch and stream), classifier, pension scores, evals
outputs/     opportunity analysis, PRD, reviews, decisions, journeys
docs/        this file
```
