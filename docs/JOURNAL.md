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

## Still to do
- Release the worker to the endpoints; time a live memo against the old 50–106 s.
- Measure routing, triage, data answers and memo quality (ask before spending).
- Update README, REPORT and DECISIONS.
- Train a memo-writing adapter and a better reader only if the tests show they're needed (ask
  before spending).
