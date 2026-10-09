"""Instructions shared by the worker's two model tasks."""

EXTRACT_SYSTEM = """Transcribe this document into the supplied JSON schema. Copy names,
descriptions and numbers as printed. Do not do arithmetic, correct totals, infer missing
values, or guess. Use null when a value is not printed, even if that makes schema validation
fail. Keep printed rows in order. Return only the JSON document.
Only two conversions are allowed:
- Dates: write every date as YYYY-MM-DD. Pages print dates day first: "29 Jan 2026" is
  2026-01-29 and "03/02/2026" is 2026-02-03.
- Money: write amounts as plain positive numbers without currency symbols or thousands
  separators. Money leaving the account is a debit; money arriving is a credit. When one
  Amount column is printed, a minus sign, brackets or "DR" means debit, and a plain or "CR"
  amount means credit; put it in that field and null in the other."""

MEMO_SYSTEM = """Write a short credit memo with sections Summary, Cash flow,
Concentration, Red flags, and Questions for the applicant. Use only facts and
numbers present in the provided analysis. Do not calculate or invent numbers.
Cite claims as [p<page>:r<row>] wherever the analysis supplies page and row
citations. Never invent citations. State when evidence is missing."""
