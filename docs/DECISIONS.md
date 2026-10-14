# Decisions — what we chose and why

Plain-English record of the main choices in LedgerLens. Each one says what we did, why, and what
we gave up. The full timeline, with times and costs, is in `BUILD_LOG.md`.

## 1. The project

- **What:** phone photos of small-business bank statements and invoices → checked rows → cash-flow
  analysis → red flags → credit memo. Runs on Runpod Serverless.
- **Why:** it is a real problem (lenders type these numbers in by hand), it needs a GPU model, and the
  customer has a clear reason to self-host: the documents are confidential and can't go to a
  US-hosted API.
- **Rejected:** a "verified credit memo" with a trained fact-checker. Published work (MiniCheck,
  2024) already does it, numbers are better checked by looking them up, and it needed about a week.

## 2. The models

- **Qwen3-VL-8B, no training, as the baseline.** A vision-language model reads the page layout and
  the text together, so it knows which number is the balance and which is money out. Open weights,
  so the customer can host it in their own region.
- **Qwen3-VL-4B, fine-tuned, as the challenger.** Half the size, so it runs on a cheaper GPU. The
  question is whether training on our documents closes the gap to the 8B.
- **Why not OCR plus a text LLM:** OCR turns the table into loose text, and the column structure
  gets lost in between.

### One base model, adapters per role

- **Choice:** one Qwen3-VL-4B engine serves the credit assistant; the trained reader is a LoRA
  switched on for page reading. vLLM uses `--enable-lora --max-loras 2 --max-lora-rank 16
  --lora-modules reader=/runpod-volume/ledgerlens/main/adapter`.
- **Why:** E1 on a Pod gave row F1 (micro) 0.9643 with LoRA vs 0.9647 merged, header accuracy
  0.9872 for both, 0 invalid outputs and 187/200 outputs identical. A second engine would add
  memory and startup work without a measured accuracy gain.
- **Trade-off:** LoRA was ~11% slower at concurrency 1 and ~8% at concurrency 8. The writer still
  uses the base model; a memo adapter is planned, not trained.

## 3. The data

### Synthetic statements and invoices (most of the data)
- **Why synthetic:** there are no public real bank statements with correct answers. When we generate
  a page, we know every number on it, so scoring is exact.
- **What is in it:** 400 fictional businesses in Singapore, Malaysia and Indonesia, six months of
  money each, local currencies and payment names (PayNow, DuitNow, BI-FAST…), English, Chinese and
  Malay words, fictional banks only.
- **Six statement layouts and two invoice layouts.** Different column orders, date styles, signs
  (one amount column vs separate debit and credit, "DR/CR" suffixes) — so the model must read, not
  learn one template.
- **Red flags planted in most businesses; about 27% have none.** If every business had a problem,
  "always flag something" would look like a good detector.
- **Photo damage at random strength** (clean, light, medium, heavy: blur, tilt, shadows), so the model
  sees phone-like pages.
- **Invoices vary** (items, quantities, prices, dates, tax). Fixed on 10 Oct: every invoice used to be
  the same one, which the model would have memorised.

### Real receipts — CORD v2
- **Why:** it is the closest public set of real photographed financial documents with correct
  answers, and its licence (CC BY 4.0) allows this use.
- **How used:** its own official split. Train receipts go into training; test receipts are only for
  testing. Receipts whose numbers we can't read without guessing are dropped and counted (we kept
  771 / 99 / 98 of 800 / 100 / 100).
- **Fairness note:** the fine-tuned model sees real receipts in training; the 8B baseline sees none.
  We say this when we show the receipt results.

### Phone photos of printed pages (test only)
- **Why:** the most honest check that is possible without real customer data — a real camera and
  real paper, with known answers. Randy prints 20 held-out pages and photographs them.

## 4. The train / validation / test split

| Split | Synthetic pages | Real receipts |
|---|---|---|
| Train | 2,396 | 771 |
| Validation | 289 | 99 |
| Test | 1,790 (1,499 unseen-layout + 291 seen-layout) | 98 |

- **Split by business, not by page.** Pages from the same business look alike (same name, same
  customers, same layout). If one page were in training and its neighbour in testing, the score
  would be too high. The code also checks that no company name appears in two splits.
- **Two statement layouts appear only in testing.** This is the "unseen layout" test: it checks that
  the model learned to read statements, not to memorise our six templates.
- **Why the test set is large:** every business that uses a held-out layout goes to test (about a
  third of all businesses), plus 10% of the others ("seen layout" test). 10% more are validation.
- **Validation** picks the best saved version of the model during training. It is never used as a
  test result.
- **Each test set is scored on its own** — unseen layouts, seen layouts, real receipts, phone photos —
  so the gap between synthetic and real stays visible instead of being averaged away.

## 5. Training settings

- **LoRA (small add-on weights), rank 16, on the language part only.** The vision part already reads
  images well; what the model lacks is our format and the accounting rules. Training fewer weights is
  cheaper and less likely to break what the model already knows.
- **16-bit training, not 4-bit.** We serve in 16-bit, so training the same way avoids a mismatch.
- **Learn from answers only.** The loss counts only the model's JSON answer, not our instructions; a
  check before training confirms this.
- **Same image size and length as serving** (up to 2 megapixels, 8,192 tokens). The rehearsal found a
  library default that shrank training pages to 512 pixels — fixed, because training on different
  pictures than you serve gives misleading results.
- **One pass over the data, learning rate 2e-4, batch of 8.** Common LoRA starting values. Validation
  loss is checked every 50 steps and the best version is kept, which guards against overtraining.
- **A rehearsal first.** A small run of every stage the night before found seven bugs (and on the day,
  two more) before we spent money on the full run.

## 6. How we score

- **Headline = the model's own answer.** A separate column shows the score after our balance check
  (code moves a row between debit and credit only when the printed balance proves it). Mixing the two
  would credit the model for work the code did.
- **Measures:** header fields correct, rows correct (precision, recall, F1), pages that add up, valid
  JSON, red flags found, seconds per page, cost per 1,000 pages.
- **All three models run on the same GPU** for the comparison, so speed and cost differences come from
  the models, not the hardware.
- **All three re-run on the fixed code.** During the first run we found the base models sometimes wrote
  an amount as a number and kept adding zeros until they ran out of space. We fixed the schema (amounts
  are now short text in a fixed shape) and re-ran all three models, not only the two that were hurt:
  the fine-tuned model's raw answers were not saved, so we couldn't prove the fix left it unchanged,
  and every reported number should come from the code that is actually served. Cost: about 3.5 h and
  $2.70 of Pod time. The first run is kept to show what the fix changed.
- **We read the failures, not just the scores.** The fine-tuned model's worst pages are heavily damaged
  photos where it reads "Jun" as "Jan" (we looked: the blur makes them hard to tell apart). Because
  rows are matched by date and amount, one wrong month makes a whole page score zero, even when every
  amount is right.
- **The served schema is the headline, and we show what it cost.** After the fix, the base models got
  worse on real receipts: asked for an amount as text, they copy rupiah as printed ("17.500", which
  means 17.5) or leave it empty. We report the final run as the result (it is what is served) and the
  first run next to it, rather than picking the better-looking number for each model.

## 7. Serving on Runpod

- **Serverless, queue endpoint** (`/run`, then check `/status`): a page takes ~15 s warm and minutes
  on a cold start — too long for one held-open connection (we saw `/runsync` drop during a cold start).
- **vLLM inside the worker, with structured output,** so every answer is valid JSON for our schema.
- **8B weights from Runpod's model cache:** 16 GB loads in under 6 seconds, no download at start.
- **Fine-tuned 4B from the network volume:** the training Pod writes it there; no rebuild needed. The
  cost: the endpoint is tied to that one data centre.
- **Zero workers when idle;** one always-on worker only for the demo hour. Max 2.
- **A `deploy` branch** for the image, so ordinary pushes don't trigger a rebuild.
- **The fine-tuned model gets its own endpoint** (same code, `MODEL_PATH` pointing at the volume), so
  the app can send the same page to both models and show speed and cost side by side. It has to be
  created in the console from the GitHub repo: a template made by hand can't pull the image Runpod
  built from GitHub (we tried; workers failed with a registry error and did not bill).
- **No Japan endpoint.** Decided 10 Oct: the data-residency answer is explained, not demonstrated.

### Global volume for the agent endpoint

- **Choice:** `cuvgn5z2av09my` uses the base 4B model cache and reader adapter on the beta global
  volume `ledgerlens-global`, so workers can start in any data centre. The merged fine-tuned
  endpoint `5078uudfkeaw2v` keeps its EU-RO-1 network volume.
- **Why:** a request waited 900 s without a GPU in EU-RO-1. Runpod's stock list showed no region
  better stocked for our GPUs. With the global volume, workers started at once in US data centres
  on RTX PRO 6000 Blackwell MIG 1g.24gb.
- **Alternatives:** waiting kept the request blocked; another region's network volume only moved
  the pin. Baking the adapter into the public image would remove the pin too, but require a
  rebuild to change it. The global volume lets us swap the adapter without a rebuild.
- **Trade-off:** object storage has no locking or atomic rename. vLLM's compile cache stays on
  container disk, so each cold start recompiles (~2 min; 119.7 s on a Pod). Allowing any data
  centre does not enforce regional document residency.

### Pages travel lossless

- **Choice:** the app sends original bytes within the pixel cap, resized PNG otherwise, and JPEG
  only above the 9 MB request budget. The worker never decodes below the pixel cap; that worker
  fix still needs release.
- **Why:** a live app test found JPEG q90 conversion plus half-size JPEG decoding turned a
  595×841 sample scan from 15/18 exact rows (PNG) into 0/18. Upscaling gave 13/18, so we skipped it.
- **Lesson:** test the full upload path. Direct tests of stored pages missed the app's image damage.

## 8. Where it runs

- **EU-RO-1 (Romania)** for training and the merged fine-tuned model's network volume. The
  agent uses a global volume and permits any data centre; live workers ran in US data centres.
  A lender would need to restrict hosting to its permitted regions.
- **32 GB GPUs** (RTX 5090 / RTX PRO 4500) for the 8B at full length; **24 GB pools** also
  serve the 4B, including RTX PRO 6000 Blackwell MIG 1g.24gb for the agent.
- **Delete training Pods, don't stop them.** A stopped Pod may not restart if its machine is busy;
  everything that matters is on the volume.

## 9. How we work

- **Claude plans and reviews; Codex writes most of the code.** Every change is read, tested, and — for
  anything visible — looked at in a screenshot. Several serious bugs passed all tests and were only
  caught by looking (a template generator, identical invoices, shrunken training pages, a 0% score
  from a validation bug).
- **Only measured numbers.** Anything not measured is marked as an estimate.
