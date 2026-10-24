# Learning notes: getting offers in

Short notes on the intake side of Quick Look. The numbers come from `make measure`.

## Event-driven versus polling

**Event-driven (push).** The mail server hands each email to our code the moment it arrives. In the demo that is a local SMTP server on port 2525. Latency is the processing time: p95 0.42 s in the demo. The cost is a process that must always run, a supervisor to restart it, and a mail route that points at it.

**Polling (pull).** A job wakes up every N minutes, lists new mail and processes it. Latency is up to N minutes plus processing. Nothing runs between scans. A crash costs nothing: the next scan sees the same unread mail.

Worked example. An offer lands at 10:07. With a 15-minute poll on the clock, the job runs at 10:15 and the brief exists at about 10:15:01. The wait is 8 minutes. The worst case is 15 minutes. The analyst's decision window is a working day, so 15 minutes is 3% of it.

How a buyer thinks about it. A secondaries team rarely acts within the hour on a first teaser. Bids are due in weeks. What hurts is missing an offer or reading it twice, not reading it 10 minutes later. Speed starts to matter only with a same-day deadline or a "first look" right a broker offers to a few buyers.

## Idempotency by Message-ID

Every email carries a `Message-ID` header that the sender sets once. We store it in bronze before any processing. If the same email comes again, from a retry, a restart, a replay or a second intake path, we see the ID and stop. Writes for one offer happen in one transaction after deleting that offer's old rows, so a half-finished run is safe to redo.

What this buys:
- At-least-once delivery becomes effectively-once processing. The SMTP sender retries until it gets `250`; the batch job leaves a message in `new/` until the pipeline finishes. Both may deliver twice; neither produces two briefs.
- Replay is safe: `radar replay` rebuilds silver, gold and briefs from bronze and gets the same rows.

Caveats a real system needs: some brokers forward the same offer with a new Message-ID. A content hash (we store `sha256`) or a code-name plus reference-date key catches that.

## What a real mailbox needs

The demo owns its own mail server. A real deal inbox lives in Exchange or Google Workspace.

- **IMAP IDLE.** The client holds an IMAP connection open and the server pushes "new mail" notices. Push latency, but you keep a long-lived connection alive and handle reconnects. Works with most providers.
- **Microsoft Graph change notifications (webhooks).** Graph calls an HTTPS endpoint you host when mail arrives in a mailbox. Subscriptions expire (about 3 days for mail) and must be renewed. You need a public endpoint, validation tokens and an app registration with mail read rights.
- **Graph delta queries or Gmail history polling.** The polling version: ask "what changed since my last token?" every N minutes. No public endpoint, no subscription to renew. This is the batch path with a real mailbox behind it.

Either way, the pipeline does not change. Only the adapter that turns "an email arrived" into `process_email(raw_bytes)` changes. That is why the experiment keeps the two paths to a few dozen lines each.

# Learning notes: tracing and feedback

## What a trace shows

A trace is the full record of one offer run, step by step: intake, parse, match, metrics, verdict, brief, validator. Each step stores what went in, what came out and how long it took. The parse step, when Claude runs it, is a "generation": it also stores the model, the prompt version, the tokens in and out, and the cost.

Worked example. Project Cedar's trace shows the parse generation took 12.5 s and used 3,397 input and 2,293 output tokens, about $0.03. The match step lists all 12 lines; Fund 9 says "unknown, description fits several managers". The verdict step shows LOOK CLOSER at score 0.75. The trace carries three scores: validator passed, data matched 0.91, band medium. When a head of team asks "why did we look at Cedar?" three weeks later, the answer is one link, not a re-run.

How a buyer thinks about it: an investment committee wants to know what you knew and when. The trace is that record for the screening step, the same idea as point-in-time data for the numbers.

## Why overrides as scores matter

Most eval sets are written by the builder. An override is different: it is a team member saying "the rule was wrong here", with a reason, on a real offer. Stored as a score on that offer's trace (`team_decision = override`, `verdict_agrees = 0`, `team_label = PASS`), it becomes a label you did not have to write.

Worked example. Layla overrides Cedar to PASS: "Seller will not move on price." The rulebook already flagged the price rule as not met, but the other rules carried the score. One override is an anecdote. Ten overrides on the price rule, filtered in Langfuse by `verdict_agrees = 0`, say the price rule should be a veto. That is the case for candidate `rulebook_v3_price_veto.yaml`, made from the team's own decisions instead of the builder's guess.

`radar keep` matters for the same reason. Without it, silence looks like agreement and you cannot tell "kept" from "not read". `radar resolve` sends `line_resolved` on the trace whose match it fixed, so the matcher's open lines become a list of cases to learn from.

Two cautions. Overrides are recorded, not learned from: a rule changes only through `radar rules test` and a review. And the team's labels are biased toward offers someone read closely; offers passed in a minute rarely get an override, even when they should.

## Tune and holdout

If you tune a rule on the same offers you test it on, the test only tells you that you tuned it. Keep a holdout: offers you never look at while changing the rule, checked once at the end.

Worked example. Rulebook v2 lowered the plan-fit bar from 0.7 to 0.55 so Willow, a good offer, would pass. On the 9 offers it was tuned on, v2 agreed 9 of 9. On 14 new holdout offers it agreed 10 of 14: the lower bar now lets old tail-end funds through. v1 got 13 of 14 on the same holdout. The fix for one offer broke a pattern the old rule caught.
