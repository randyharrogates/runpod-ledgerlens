export const tour = [
  {
    "route": "/",
    "readStep": 2,
    "title": "Read statements",
    "words": "This is a small business's bank statement, photographed on a phone. I load the sample business, pick our trained small model and read every page. Each line lands in the table on the right, and the app re-adds every running balance: a tick means the printed balance matches. The orange line is a returned cheque, flagged on its own.",
    "start": 0,
    "end": 40,
    "highlight": "rows",
    "readSteps": [
      2
    ]
  },
  {
    "route": "/",
    "readStep": 2,
    "title": "Read statements",
    "words": "If a line is wrong, the analyst fixes the cell and everything after it recomputes. Nothing the model reads is final until a person has had the chance to check it.",
    "start": 40,
    "end": 60,
    "highlight": "rows",
    "readSteps": [
      2
    ]
  },
  {
    "route": "/",
    "readStep": 3,
    "title": "Read statements",
    "words": "From those lines, plain code, not the model, works out cash flow, the biggest customers and the red flags, each linked to its line. The model only writes the memo's wording, and every claim carries a reference I can click to jump back to the evidence.",
    "start": 60,
    "end": 90,
    "highlight": "flag",
    "readSteps": [
      3,
      4
    ]
  },
  {
    "route": "/compare",
    "readStep": null,
    "title": "Compare models",
    "words": "Same page, three models at once. On a layout none of them had seen, both untrained models read incoming payments as money going out on 7 of the 18 lines. Our trained small model got all 18 right.",
    "start": 90,
    "end": 120,
    "highlight": "compare",
    "readSteps": []
  },
  {
    "route": "/results",
    "readStep": null,
    "title": "Results",
    "words": "Across 1,499 pages in held-back layouts, the trained small model reads 96.5% of lines right, meaning the date and the amount in or out are exactly correct, against 57.8% for a model twice its size. It is weakest on heavily damaged photos, at 86.8%, and on real shop receipts, at 76.5%. These are the pages it got most wrong.",
    "start": 120,
    "end": 145,
    "highlight": "results",
    "readSteps": []
  },
  {
    "route": "/cost",
    "readStep": null,
    "title": "Cost calculator",
    "words": "Reading in batches costs 26 cents per 1,000 pages on the trained small model, half the big model's 52. The serverless GPUs sleep at no cost when nobody is using them, and we pay per second of reading.",
    "start": 145,
    "end": 165,
    "highlight": "cost",
    "readSteps": []
  },
  {
    "route": "/training",
    "readStep": null,
    "title": "Training",
    "words": "And the whole pipeline can be rerun from here. The recorded run trained for 86 minutes for about a dollar, and a new run shows its price and a spending limit before any GPU is rented.",
    "start": 165,
    "end": 180,
    "highlight": "training",
    "readSteps": []
  }
] as const;
