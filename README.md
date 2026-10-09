# LedgerLens — offline Phase 1

Python 3.11, Decimal extraction schemas, reproducible synthetic documents,
cited analytics, and extraction scoring. No model, GPU, Runpod, or network is
needed to run the pipeline once Python dependencies are installed.

```sh
uv sync
uv run python -m ledgerlens.datagen --out data/synth --n-businesses 3 --seed 7 --no-render
uv run python -m ledgerlens.eval.score --pred data/synth --gold data/synth --out data/eval/results.md
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Omit `--no-render` to use a local Chromium binary. The renderer searches the
macOS Playwright cache (headless shell, then Chromium), then Google Chrome.
It disables GPU and background networking; it never downloads a browser.
Rendering fails clearly when the binary is absent or blocked by the sandbox.

Each statement page has HTML, schema JSON, and `.meta.json` provenance. PNGs
are written only when rendering succeeds. Metadata records local and global
transaction indices, anomaly labels, the printed and true closing balances,
and seeded image operations. With `--no-render`, degradation is selected but
explicitly recorded as unapplied. Each business also has two invoice pages.
Choose a fresh output directory for each generation run.

`splits.json` lists page ids under `train`, `val`, and `test`, along with the
business assignments and page registry. Businesses are assigned before layouts;
`stmt_e` and `stmt_f` are test-only. Small datasets reserve one test business,
and datasets with at least three businesses also reserve one validation business.

`ledgerlens.analytics.analyze` accepts statement pages and optional page ids.
Use one business and currency per analysis. Monthly cash flows, observed
transaction balance statistics, payer concentration, reconciliation failures,
and anomaly findings carry zero-based page/row citations. Printed balance
errors never contaminate the true balance carried to the following page.

The scorer writes Markdown and a JSON companion. Missing predictions and
invalid documents count as failures. Exact date/debit/credit matching consumes
each gold row once; item rows use description, quantity, price, and amount.
`page_reconciliation_rate` measures pages that actually add up, while
`reconciliation_accuracy` compares that decision with gold. Thus gold-vs-gold
scores perfectly even though intentionally misprinted pages do not reconcile.
Anomaly recall is evaluated across whole businesses, preserving cross-page
round trips and period-wide customer concentration. Keep reports outside the
gold document directory.

In the implementation sandbox, normal offline `uv sync` could not resolve
Python 3.11 numpy wheels from cache. Verification used already installed local
Python 3.11 runtime dependencies and cached pytest/ruff, with `UV_NO_SYNC=1`,
`UV_OFFLINE=1`, and a writable temporary uv cache. This is a local verification
workaround, not a change to project dependency requirements. No network install
was attempted. Chromium rendering was skipped because the macOS sandbox blocked
its startup.
