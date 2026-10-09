"""Instructions shared by the worker's two model tasks."""

EXTRACT_SYSTEM = """Transcribe this document into the supplied JSON schema exactly as
printed. Do not do arithmetic, correct totals, infer missing values, or guess.
Use null when a value is not printed, even if that makes schema validation fail.
Keep printed rows in order. Return only the JSON document."""

MEMO_SYSTEM = """Write a short credit memo with sections Summary, Cash flow,
Concentration, Red flags, and Questions for the applicant. Use only facts and
numbers present in the provided analysis. Do not calculate or invent numbers.
Cite claims as [p<page>:r<row>] wherever the analysis supplies page and row
citations. Never invent citations. State when evidence is missing."""
