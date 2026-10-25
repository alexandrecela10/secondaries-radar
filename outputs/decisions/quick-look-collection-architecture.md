---
decision_id: "QL-ARCH-1"
date: "2026-10-24"
product_area: "Secondaries Radar"
feature: "Offer Check intake"
prd_stage: "Solution Review"
decided_by: "Build agent, applying the PRD section 9 rule. Pending PM review."
status: "active"
supersedes: ""
---

# Decision: Collect Offer Check offers with a batch job that polls the mailbox every minute

## What was decided

Use the batch path: a scheduled job scans the mailbox every N minutes. Set N to 1 minute, not 15.

## Why

- **The rule picks batch.** Both paths pass criteria 1 to 3. The tie goes to fewer moving parts: batch has 2, real-time has 3.
- **N = 15 meets criterion 1 but not Q-7.** p95 at 15 minutes is 14.2 minutes, under the 48-minute limit. Q-7 asks for a brief within 2 minutes of arrival, so 15 minutes fails it.
- **N = 1 meets both.** p95 is 58 seconds. It still needs no always-on process.
- **Parity found a real bug.** The first run differed in `fit_score` in the last float digits. Python set order changes with each process's hash seed. Sorting the keys fixed it, and a regression test now covers it.

## Measured results

Same pipeline function (`pipeline.process_email`) behind both paths. 10 emails (9 offers and 1 non-offer), 3 rounds, offline parser, one laptop. Raw data: `quicklook/measurements/*.json`.

| # | Criterion | Real-time (SMTP server) | Batch (Maildir poll) | Pass rule |
|---|---|---|---|---|
| 1 | p95 latency, send to brief written | 0.42 s (30 emails) | 9.3 s at N = 5 s (30 emails, real); 58 s at N = 1 min, 14.2 min at N = 15 min, 57 min at N = 60 min (simulated) | Under 48 min (10% of an 8-hour day). Both pass up to N = 15 min. N = 60 min fails |
| 2 | Output parity on the same emails | 11 of 11 tables identical (silver, gold, brief hashes) | same | Identical: pass |
| 3 | Recovery after kill -9 mid-run, then restart | 0 duplicates, 0 missed. 1 email was mid-run; the sender retried once | 0 duplicates, 0 missed. 1 email was mid-run; 9 still waited in `new/` | Both pass |
| 4 | Moving parts | 3: listener process, supervisor, mail route | 2: scheduler entry, mailbox | Batch fewer |
| 5 | Path-specific lines of code | 49 | 47 | Shared pipeline: 256 |
| 6 | Always-on hours per month | 730 | 0. Busy time: 0.3 h at N = 15 min, about 5 h at N = 1 min (0.41 s cold start per run) | Batch lower |
| 7 | Replay from bronze, 30 emails | 8.2 s | 9.5 s | Same code, same cost |

**Method notes.**
- Simulated batch latency: 20,000 arrivals spread evenly over 09:00 to 17:00, a scan on the clock every N minutes, plus the measured p95 processing time (0.44 s).
- The kill test slowed each run by 0.8 s so the kill landed mid-run. The kill came 2.5 s after start, followed by a restart without the delay.
- Parity compared row hashes per table and excluded arrival-specific columns (received time, channel, written time).

## What was NOT chosen

| Alternative | Why rejected |
|---|---|
| Real-time SMTP listener | Passes 1 to 3 but needs an always-on process, a supervisor and a mail route. Its 0.4 s latency buys nothing on a decision that takes hours or days. |
| Batch every 15 minutes | Passes criterion 1 but breaks Q-7 (2 minutes). Keep it as the fallback if the scheduler cost matters. |
| Batch every 60 minutes | Fails criterion 1: p95 57 minutes is over 48. |
| Both paths in production | Doubles the intake code paths to maintain. Parity means either one gives the same output. |

## Consequences

**This decision enables:**
- No public endpoint, no listener and no subscription renewal. In production, the job calls a Microsoft Graph delta query or IMAP search every minute.
- Crash recovery for free: unread mail stays unread until the pipeline finishes.

**This decision prevents:**
- Sub-second briefs. Typical latency is 30 seconds, worst case about 60.

## Open questions remaining

- [ ] Confirm Q-7's 2-minute target with practitioners. If 15 minutes is fine, use N = 15 and cut scheduler runs by 15 times. @Alexandre
- [ ] Measure on a real mailbox (Graph delta query). Its latency adds to the scan interval. @Alexandre

## Review trigger

Revisit if any of these becomes true:
- **A same-hour deadline appears.** For example, a broker gives a "first look" right that expires within the hour, or a same-day bid round. Then each minute counts, and the real-time path (or Graph webhooks) earns its moving parts.
- **Volume grows past what one scan clears in a minute.** At about 0.4 s per offer, that is over 100 offers arriving together.
- **The mailbox provider rate-limits polling** below one call per minute.

---

*Filed to: `outputs/decisions/quick-look-collection-architecture.md`*
*Related PRD: `outputs/prds/secondaries-radar-solution-review.md` (sections 5.2 Q-1 and Q-7, section 9)*
