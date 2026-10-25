# Journey: Quick Look, associate triages Project Cedar

**Persona:** Layla, secondaries associate. Reads 5 to 10 broker offers a week. Reports to the head of secondaries, who decides on NDAs.
**Job to be done:** When a broker sends a sale offer, I want a sourced "look closer or pass" call with its reasons, so I can tell my head of team by 10:00 whether to sign the NDA.
**Success:** decision sent within 30 minutes of the email, every number traceable.

| Time | Step | What Layla does | What the system does | Command |
|---|---|---|---|---|
| 09:00 | Offer arrives | Nothing | Picks up the Project Cedar email, parses it, identifies funds by their numbers, applies the rulebook | `make journey` (sends the email, batch picks it up) |
| 09:02 | Reads the verdict | Reads one sentence | "LOOK CLOSER: fits our rules, and the price looks right by our own data. High confidence. Still open: 1 fund (9% of value) not identified." | `radar brief cedar` |
| 09:05 | Checks why | Reads the reasons; names Fund 9 if they know it | Rules met and not met, each with number and source. Fund 9 sits in the reasons with its two candidates | `radar why cedar`, `radar resolve cedar 9 <fund_id>` |
| 09:08 | Checks the price | Reads what our own reports say since 30 June | Two companies sold above their June value: the 3 funds we own are worth 5.5% more than the broker's figure, so high 80s is inside our range | `radar why cedar` (price lines), `radar brief cedar` |
| 09:12 | Checks a match | Opens Fund 4 | Side by side: offer ratios vs our fund B ratios at 30 June | `radar match cedar 4` |
| 09:20 | Decides | Keeps the verdict, or overrides with a reason | Records the decision with rulebook version | `radar keep cedar` / `radar override cedar pass --reason "..."` |
| 09:25 | Tells the head of team | Pastes a short message | Decision and ask first, then 4 or 5 plain facts, every number sourced | `radar memo cedar` |

**The other direction:** Hornbeam looks fine at the June values, but our reports show two write-downs since. The price check says too expensive, and the verdict is PASS.

**Out of this journey:** pricing after the NDA, real mailbox, Excel export.
