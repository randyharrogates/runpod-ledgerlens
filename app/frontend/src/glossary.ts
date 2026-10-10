export const glossary = {
  'row F1': 'Statements match a line on DATE + MONEY OUT + MONEY IN. Description and running balance are not compared line by line; balance is covered separately by page adds up. Invoice and receipt items match description + quantity + unit price + amount. Row F1 combines precision and recall.',
  precision: 'The share of lines the model wrote that match real lines.',
  recall: 'The share of real lines the model found.',
  reconciliation: 'The page’s running balance adds up.',
  'header fields': 'Fields at the top of the document, such as dates, currency and account details.',
  'red flags': 'Planted warning signs found (recall), and how many reported warnings are planted signs (precision).',
  'average per page': 'Each page has equal weight here. The headline combines matched lines across all pages, so the figures differ slightly.',
  'cold start': 'the GPU was asleep and had to wake up',
};
