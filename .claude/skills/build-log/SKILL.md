---
name: build-log
description: Append an entry to docs/BUILD_LOG.md after every meaningful step (decision, resource created, Codex task reviewed, deploy, test result, spend). Use whenever something was done or decided in this project.
---

# Build log

Standing instruction: **record every step as we go**. The log is the source for the final report
and the demo walkthrough, so it must be factual and timed.

1. Get the real time: `date +%H:%M` (SGT). Never estimate timestamps.
2. Append under today's `## <Day> <date>` heading (create it if missing) in the right subsection
   (Choosing / Accounts and infrastructure / Code / Endpoint / Training / Evaluation / Docs / Spend).
3. Entry format: `- **HH:MM — Short title.** What was done, why, and the evidence` — IDs, exact
   commands (never keys), commit hashes, test counts, measured numbers, review findings and fixes.
4. Update the **Spend so far** table whenever GPU time was used (`runpodctl user` for the balance;
   `runpodctl billing` for history).
5. Commit with a `docs(log): …` message. Pushing `main` is fine once the endpoint tracks `deploy`.

Keep entries short: one to four lines. Record failures and dead ends too: how a problem was found
and fixed is worth as much as the fix.
