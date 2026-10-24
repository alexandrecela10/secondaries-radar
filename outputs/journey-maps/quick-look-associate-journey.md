# Journey: Quick Look, associate triages Project Cedar

**Persona:** Layla, secondaries associate. Reads 5 to 10 broker offers a week. Reports to the head of secondaries, who decides on NDAs.
**Job to be done:** When a broker sends a sale offer, I want a sourced "look closer or pass" call with its reasons, so I can tell my head of team by 10:00 whether to sign the NDA.
**Success:** decision sent within 30 minutes of the email, every number traceable.

| Time | Step | What Layla does | What the system does | Command |
|---|---|---|---|---|
| 09:00 | Offer arrives | Nothing | Picks up the Project Cedar email, parses it, identifies funds by their numbers, applies the rulebook | `make journey` (sends the email, batch picks it up) |
| 09:02 | Reads the verdict | Reads one sentence | "LOOK CLOSER, medium confidence: 1 fund unmatched (9% of value), close to the line." | `radar brief cedar` |
| 09:05 | Checks why | Reads the reasons | Rules met and not met, each with number and source | `radar why cedar` |
| 09:10 | Checks a match | Opens Fund 4 | Side by side: offer ratios vs our fund B ratios at 30 June | `radar match cedar 4` |
| 09:15 | Fixes the open line | Picks the right candidate for Fund 9 | Recomputes verdict and confidence | `radar resolve cedar 9 <fund_id>` |
| 09:20 | Decides | Keeps the verdict, or overrides with a reason | Records the decision with rulebook version | `radar keep cedar` / `radar override cedar pass --reason "..."` |
| 09:25 | Tells the head of team | Pastes one paragraph | A short summary with sourced numbers | `radar memo cedar` |

**Out of this journey:** pricing after the NDA, real mailbox, Excel export.
