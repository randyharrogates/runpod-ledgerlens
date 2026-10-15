# Journal: building the chat agent

Plain-English story of the chat-agent work, step by step: what we did, why, and what we weighed.
Exact numbers, IDs and commands are in `docs/BUILD_LOG.md`; this file explains the thinking.
It is updated as each step finishes, until the work is done.

## Where we started
LedgerLens already read photos of bank statements and invoices into rows (a fine-tuned Qwen3-VL-4B),
worked out cash flow and red flags in code, and wrote a credit memo. Each step was a separate button.
The goal now: **one chat box**. The analyst types a request, with or without pages attached, and a
small team of agents does the work on one Runpod Serverless GPU, quickly.

## Step 1: Choose models for each agent
- **What:** surveyed open models for each job (routing, page triage, reading, tool use, writing).
- **Why:** to avoid training something when a ready model would do, and to avoid picking a model
  we can't legally or practically serve.
- **Considerations:** must run on one 24–32 GB GPU with our vLLM version; licence must allow
  commercial use; must handle English, Malay/Indonesian and some Chinese; must give reliable JSON.
  Some strong OCR models were ruled out on licence (AGPL, revenue caps, region limits) or because
  vLLM can't load adapters for them.
- **Decision:** keep Qwen3-VL-4B as the single base model. Every agent uses it; the trained reader
  is an add-on (a LoRA adapter) switched on only for reading. Nothing beat this under our limits.

## Step 2: Design the agents and what runs at the same time
- **What:** router (decides what the user wants), page triage (is it a financial page?), reader,
  analyst (code), data agent (answers questions with five read-only lookup tools), writer (memo in
  five sections), checker (code).
- **Why this split:** a model only where vision or language is needed. Arithmetic, lookups and
  checks are code, because code doesn't invent numbers.
- **Speed choices:** start reading pages the moment they are attached, before the user presses
  send; wake the GPU when the page opens; run the router while the numbers are computed; write the
  five memo sections at once; check each section as it finishes and rewrite only a failed one.
- **Considered and skipped:** guessing the route early (wastes work when wrong); sharing image work
  between triage and reader (the adapter changes it, so it can't be reused).

## Step 3: Fix slow streaming first
- **What:** memos took 50–106 s live. Reading the Runpod SDK showed it makes one web request per
  piece of text the worker sends, and the worker sent every word separately.
- **Fix:** send text in batches (every 64 characters or 200 ms).
- **Why first:** every chat answer streams text, so this would have slowed everything after it.

## Step 4: Shared agent library
- **What:** moved the checker, red-flag wording and data tools into `src/ledgerlens/agent/`, plus
  the prompts and input limits.
- **Why:** the app and the worker must use the same checks; one copy means they can't drift.

## Step 5: The agent inside the worker
- **What:** two new worker tasks: `read` (pages → rows, each page independently) and `agent`
  (one chat turn). One stream of events shows each step and its time. A plain prompt now goes to
  the agent.
- **How it was built:** Codex wrote it from a written brief; Claude reviewed the diff and ran all
  tests.
- **Review fixes:** turned on tool calling in vLLM (it was missing); one bad page no longer fails
  the whole turn; capped tool calls per round, rows per document and memo length after a security
  review showed a huge input could tie up the CPU.

## Step 6: Prove the adapter works as well as the merged model (gate E1)
- **What:** on a rented GPU (a Pod), read 200 unseen pages with the merged fine-tune and with the
  base model + adapter, and compare.
- **Why:** the one-engine design only works if the adapter is as accurate. If not, we'd need a
  second engine, which costs memory and start-up time.
- **Result:** lines read right 0.9643 vs 0.9647; 187 of 200 outputs identical; ~8–11% slower.
  Passed. One engine it is.
- **Also measured:** how long the server takes to start with different settings, because start-up
  time is what a user waits for when the GPU was asleep.

## Step 7: The chat page in the app
- **What:** a new first page, Assistant: chat on the left, attached pages on the right with live
  status, memo sections filling in at once, clickable references back to the rows, a Stop button,
  and time/cost per answer.
- **Why these details:** a credit analyst must be able to check every number, so references open
  the exact row; showing each step and its time makes the speed work visible.
- **Offline demo:** replays real recorded agent runs, clearly labelled "Recorded run", so the demo
  works without a GPU and never shows made-up output.
- **Checked by looking:** screenshots at desktop and phone width.

## Step 8: Run the whole agent live, three times
- **What:** on the same rented GPU, ran nine real chat turns (memo, data questions, an edit, a
  general question, no documents, off-topic) against the real model.
- **Why:** unit tests use a fake model. Only the real model shows whether it follows the
  instructions.
- **Run 1:** the router said "no documents loaded" when five were, and its JSON overflowed. Fixed
  by letting code decide whether documents exist and banning free whitespace in forced JSON.
- **Run 2:** no errors, but reading the answers showed four were wrong (a memo with 2 of 5
  sections, "largest payer" looking at money going out, no currency, no row references). Fixed by
  always writing all five sections and making the lookup tools say what "in" and "out" mean.
- **Run 3:** every route right; memo in 3.9 s, data answers in about 2 s, fixed replies under 1 s.
- **Lesson:** "no errors" is not "right". Every answer was read before calling it done.
- **What's left:** the small model still writes some vague phrases and once echoed its own
  instructions. That is the reason for the planned memo-writing adapter, to be tested before training.
- **Cost:** about $2.27 for three hours of GPU; the Pod was deleted straight after.

## Step 9: Real recordings for the offline demo
- **What:** the third run's answers are saved and replayed by the app when no GPU is connected.
- **Why:** a demo should never show made-up output; these are real model answers, labelled
  "Recorded run".

## Step 10: Every log in one place, inside the app
- **What:** a "Logs" panel on every page shows, live, what the app server, the browser, the
  Runpod worker and the model engine (vLLM) did. Each chat answer has a "Logs for this turn" link
  and, when it ran on Runpod, its job id.
- **Why:** when an answer was slow or failed, the only way to see why was the Runpod console,
  and the browser's own errors were invisible. A user or engineer should be able to answer "what
  happened on this turn?" without leaving the page.
- **How:** the worker sends its log lines (model call times, token counts, checker rewrites, tool
  errors, exceptions, vLLM warnings) inside the same stream as the answer, batched so they don't
  add a request per line. The server keeps the last 2,000 lines in memory, hides the API key, and
  never logs images, prompts, documents or answers.
- **What was weighed:** Runpod has a public API for a worker's own console log; it is wired in
  but off by default (`RUNPOD_WORKER_LOGS=1`), because worker logs are not tied to a single job
  and it adds polling. A security review then found that anyone on the network could read every
  session's logs; now each person sees only their own session's lines plus app-wide ones, and
  session ids are masked in messages.
- **Checked by looking:** screenshots at desktop and phone width; filters, search, "this turn
  only", and pause-on-scroll all work.

## Step 11: Let the agent run in any data centre, and make the chat easier to read
- **What happened:** the first live test waited 15 minutes with no GPU. The agent endpoint
  had its files on a network volume, and a network volume ties every worker to one data
  centre (EU-RO-1), which had no free GPU.
- **What was weighed:** moving to another region's network volume, or a "global volume"
  (Runpod beta) that every data centre can read. Runpod's own stock list showed no region
  better stocked than EU-RO-1 for our GPUs, so moving would only swap one pin for another.
  The global volume removes the pin. It costs $0.09 per GB a month, and our file is small.
- **What we did:** created the global volume, used a cheap Pod for five minutes (about
  $0.02) to copy the 139 MB reader adapter onto it, checked every file's checksum, deleted
  the Pod, and pointed the endpoint at the global volume with all data centres allowed.
  The GPU list went from "unavailable / low" to "medium / high supply".
- **The trade-off:** the global volume is object storage, so vLLM's compile cache can't
  safely live there. Each cold start now redoes about 2 minutes of compiling.
- **Logs:** released the logs feature to the workers, and switched on the Runpod
  worker-log feed by default. A security check pointed out that a worker's log is not tied
  to one job, so we only record lines from after the job started, and pause when another
  session is using the same endpoint.
- **Chat colours:** every kind of thing now has its own colour: your messages are blue
  bubbles; the agent's answer has a coloured edge by type (memo purple, data blue, general
  green, fixed replies orange, errors red); the step list folds into one line when done; the
  logs drawer is darker with coloured sources. Added a typing indicator, copy buttons,
  "Try again", a welcome card and a composer that stays at the bottom. Checked on a desktop
  and phone-sized screen.

## Step 12: The first real test of the whole thing, and what it caught
- **What we did:** used the app in a browser against the live endpoint: read the sample
  business, asked for a memo, asked a data question, asked for an edit, pressed Stop.
- **Speed:** the memo took 7.9 seconds (it used to take 50–106) and cost about a third of a
  cent. A data answer took 4 seconds. The very first call after a quiet spell still waits
  about 7.5 minutes while a GPU starts up and loads the model.
- **What it caught (and why we look, not assume):**
  1. The chat page crashed when the checker left a note on a memo section, because the note
     arrived as a small object, not text. Fixed and tested.
  2. The bigger one: the reader misread a small, low-quality page badly (0 of 18 rows right),
     and the memo then blamed the business for "discrepancies" that were really reading
     mistakes. We tested the same page three ways on the live endpoint: as stored (15 of 18
     right), enlarged (13 of 18) and as the app was sending it, a compressed JPEG (0 of 18).
     The app had been squeezing every page into JPEG, and the worker was also halving JPEG
     pages before reading them. Both now keep the page sharp. After the fix the memo named
     exactly the problems planted in the sample.
- **Lesson:** every test before this used the stored pages directly, so none of them went
  through the app's own image step. Only an end-to-end run showed it.

## Step 13: Fresh demo recordings and up-to-date docs
- **What we did:** recorded the demo questions again from the live endpoint (data questions and
  the general answer are now the live versions, with amounts in SGD). Updated the README, the
  report and the decisions with the credit assistant, the move to a global volume, the live
  timings and the image bug.
- **One more small fix:** the memo writer sometimes ends a heading with a full stop, and the
  checker then rewrote a good section for nothing. The checker now ignores that full stop.
- **What a cancel costs:** pressing Stop cancels the job at once, but the worker keeps running
  for about 2 minutes while it finishes what it had started, then goes idle by itself.

## Step 14: Testing every part of the assistant on the live endpoint
- **What we did:** built four test sets and ran them all against the live endpoint in about
  6 minutes for about $0.07: 180 messages to the router (in English, Indonesian, Malay and
  Chinese, including 20 attempts to trick it), 156 pages for the page check, 95 questions about
  the data, and 30 memos each followed by an edit.
- **Why this way:** a test switch makes the worker stop right after the step being tested (for
  example after routing), so we pay for 2 seconds per message instead of a whole memo. The data
  and memo tests use the true rows of our synthetic businesses, so a wrong answer is the
  assistant's fault, not the reader's.
- **What we learned:**
  - The page check was right every time; the router 91% (memo requests worded as an
    "assessment" were missed, mostly in Malay and Chinese).
  - Data answers 66%: two tools were missing (one to list red flags, one to find the single
    largest payment), and on Chinese-named businesses the model swapped the name from the tool
    for a famous brand name. That last one is the most worrying, and it is why a code check
    for names comes next.
  - Memos: every section, heading and citation right, but "make it shorter" was followed in
    only 5 of 30 edits, and 5 of 30 memos kept an invented percentage the checker had flagged.
- **What we decided:** every failure has a cheap fix in code or prompts, so we fix those first
  and test again before training anything new.

## Still to do
- Release the worker fixes (sharp JPEG pages, heading check), then re-record the memo demo.
- Fix what the tests found, release, and run the tests again.
- Train a memo-writing adapter and a better reader only if the tests show they're needed (ask
  before spending).
