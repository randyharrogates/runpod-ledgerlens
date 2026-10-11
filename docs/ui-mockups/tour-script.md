# Presenter mode — the 3-minute tour (exact words; every number is measured)

1. Read statements · 0:00–0:40 · route / · step 2
"This is a small business's bank statement, made to look like a phone photo; the business and bank are fictional. I load the sample business, pick our trained small model and read every page. Each line lands in the table on the right, and the app re-adds every running balance: a tick means the printed balance matches. The orange line is a returned cheque, flagged on its own."

2. Read statements · 0:40–1:00 · route / · step 2 (point at an editable cell)
"If a line is wrong, the analyst fixes the cell and everything after it recomputes. Nothing the model reads is final until a person has had the chance to check it."

3. Read statements · 1:00–1:30 · route / · steps 3 then 4
"From those lines, plain code, not the model, works out cash flow, the biggest customers and the red flags, each linked to its line. The model only writes the memo's wording, and every claim carries a reference I can click to jump back to the evidence."

4. Compare models · 1:30–2:00 · route /compare
"Same page, three models at once. On a layout none of them had seen, both untrained models read incoming payments as money going out on 7 of the 18 lines. Our trained small model got all 18 right."

5. Results · 2:00–2:25 · route /results
"Across 1,499 pages in held-back layouts, the trained small model reads 96.5% of lines right, meaning the date and the amount in or out are exactly correct, against 57.8% for a model twice its size. It is weakest on heavily damaged photos, at 86.8%, and on real shop receipts, at 76.5%. These are the pages it got most wrong."

6. Cost calculator · 2:25–2:45 · route /cost
"Reading in batches costs 26 cents per 1,000 pages on the trained small model, half the big model's 52. The serverless GPUs sleep at no cost when nobody is using them, and we pay per second of reading."

7. Training · 2:45–3:00 · route /training
"And the whole pipeline can be rerun from here. The recorded run trained for 86 minutes for about a dollar, and a new run shows its price and a spending limit before any GPU is rented."
