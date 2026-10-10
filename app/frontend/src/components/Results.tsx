import React from 'react';

type Metrics = Record<string, number | string | null>;
type Summary = {
  split_labels: Record<string, string>;
  splits: Record<string, Record<string, Metrics> & { status?: string }>;
};
const models = [
  ['8b', '8B, no training'],
  ['4b', '4B, no training'],
  ['4b-ft', '4B fine-tuned'],
];
const metrics = [
  ['row_f1', 'Rows read exactly right (row F1)', 'rate'],
  ['row_precision', 'Rows written that are right (precision)', 'rate'],
  ['row_recall', 'Real rows found (recall)', 'rate'],
  ['row_f1_checked', 'Rows read exactly right after the balance check', 'rate'],
  ['header_accuracy', 'Header fields right', 'rate'],
  ['reconciliation', 'Page adds up', 'rate'],
  ['valid_json', 'Valid JSON', 'rate'],
  ['anomaly_recall', 'Red flags found (recall)', 'rate'],
  ['anomaly_precision', 'Red-flag precision', 'rate'],
  ['seconds_per_page', 'Seconds per page (batched)', 'seconds'],
  ['cost_per_1000_pages', '$ per 1,000 pages', 'dollars'],
];

// Headline set first; JSON key order is alphabetical.
const order = ['test_unseen_layout', 'test_seen_layout', 'test_cord'];

export function ResultTable({ value }: { value: unknown }): React.ReactElement {
  const summary = value as Summary;
  const labels = Object.entries(summary.split_labels ?? {}).sort(([a], [b]) => (
    (order.indexOf(a) + 1 || order.length + 1) - (order.indexOf(b) + 1 || order.length + 1)
  ));
  const any = Object.values(summary.splits ?? {})
    .flatMap(entries => models.map(([model]) => entries?.[model])).find(Boolean);
  return <div className="evaluation-results">
    {any && <p className="evaluation-note">All three models ran on the same GPU ({any.gpu_name ?? '—'}), batched, same prompt and schema. Row F1 is the model&apos;s own answer. Cost at ${typeof any.price_per_hour === 'number' ? any.price_per_hour.toFixed(2) : '—'}/h.</p>}
    {labels.map(([split, label]) => {
      const entries = summary.splits?.[split];
      if (!entries) return null;
      const first = models.map(([model]) => entries[model]).find(Boolean);
      if (entries.status === 'empty_set' || !first) {
        return <section key={split}><h2>{label} · 0 pages</h2><p>No pages in this set.</p></section>;
      }
      return <section key={split}>
        <h2>{label} · {Number(first.pages).toLocaleString('en-US')} pages</h2>
        <div className="table-scroll"><table>
          <thead><tr><th scope="col">Metric</th>{models.map(([id, name]) => <th scope="col" key={id}>{name}</th>)}</tr></thead>
          <tbody>{metrics.map(([key, label, format]) => {
            const values = models.map(([id]) => entries[id]?.[key]);
            const numbers = values.filter((n): n is number => typeof n === 'number' && Number.isFinite(n));
            // Rows that don't apply to this set (no statements: no reconciliation or red flags).
            if (!numbers.length) return null;
            const best = format === 'rate' ? Math.max(...numbers) : Math.min(...numbers);
            return <tr key={key}><th scope="row">{label}</th>{values.map((n, i) => {
              const valid = typeof n === 'number' && Number.isFinite(n);
              const text = !valid ? '—' : format === 'rate' ? `${(n * 100).toFixed(1)}%` : format === 'dollars' ? `$${n.toFixed(2)}` : n.toFixed(2);
              return <td key={models[i][0]}>{valid && n === best ? <strong>{text}</strong> : text}</td>;
            })}</tr>;
          })}</tbody>
        </table></div>
      </section>;
    })}
  </div>;
}
