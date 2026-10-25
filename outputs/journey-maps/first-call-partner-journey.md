# Journey: Seller Watch, partner plans the week

**Persona:** Omar, partner, owns relationships with large US public pensions. Plans the week on Monday at 08:00.
**Job to be done:** When I plan my week, I want to know which pensions show signs they may sell, with the evidence, so I can get the team ready before an offer arrives.
**Success:** the weekly review takes 10 minutes; every flag shows a real quote, link and date.

| Time | Step | What Omar does | What the system does | Command |
|---|---|---|---|---|
| Overnight | Collect | Nothing | Batch job fetches news, classifies it, updates pension scores | `make collect-batch` |
| 08:00 | Reads the list | Scans PREPARE and WATCH | Weekly list, PREPARE first; "no pension crossed PREPARE" if none | `firstcall weekly` |
| 08:03 | Checks evidence | Opens the top pension | Each signal: quote, outlet, date, link, weight, decay | `firstcall why <pension>` |
| 08:06 | Sees the history | Looks at the timeline | Signals over time against the PREPARE line | `firstcall timeline <pension>` |
| 08:08 | Decides | Marks "prepare" (assign an analyst) or dismisses with a reason | Records the decision; dismissals feed rule review | `firstcall act <pension> prepare --owner layla` / `firstcall dismiss <pension> --reason "..."` |

**Out of this journey:** holdings and price for the pension, outreach drafting, CRM.
