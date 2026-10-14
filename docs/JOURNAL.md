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

## Still to do
- Live end-to-end test of the agent on the Pod; save its runs as the demo recordings.
- Stop the Pod; release the worker to the endpoints; time a live memo against the old 50–106 s.
- Measure routing, triage, data answers and memo quality (ask before spending).
- Update README, REPORT and DECISIONS.
- Train a memo-writing adapter and a better reader only if the tests show they're needed (ask
  before spending).
