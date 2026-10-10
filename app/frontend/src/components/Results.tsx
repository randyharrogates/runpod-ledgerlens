import React, { useEffect, useState } from 'react';
import { Term } from './Term';
import { models as modelChoices } from '../models';
import { Example, getJSON, LineTable, scoreKey } from './Compare';

type Metrics = Record<string, number | string | null>;
export type Summary = {
  split_labels: Record<string, string>;
  splits: Record<string, Record<string, Metrics> & { status?: string }>;
  // Sets run differently from the batch (e.g. live, one page at a time) say how.
  split_notes?: Record<string, string>;
};
const models = [
  ['8b', 'Big model'],
  ['4b', 'Small model'],
  ['4b-ft', 'Trained small model'],
];
const metrics = [
  ['row_f1', 'Lines read right (row F1)', 'rate'],
  ['row_precision', 'Lines it wrote that are right (precision)', 'rate'],
  ['row_recall', 'Real lines it found (recall)', 'rate'],
  ['row_f1_checked', 'Lines read right after the balance check', 'rate'],
  ['header_accuracy', 'Header fields right', 'rate'],
  ['reconciliation', 'Page adds up', 'rate'],
  ['valid_json', 'Valid JSON', 'rate'],
  ['anomaly_recall', 'Red flags found (recall)', 'rate'],
  ['anomaly_precision', 'Red-flag precision', 'rate'],
  ['seconds_per_page', 'Seconds per page', 'seconds'],
  ['cost_per_1000_pages', '$ per 1,000 pages', 'dollars'],
];

// Headline set first; JSON key order is alphabetical.
const order = ['test_unseen_layout', 'test_seen_layout', 'test_cord', 'test_phone', 'test_phone_clean'];

export function ResultTable({ value }: { value: unknown }): React.ReactElement {
  const summary = value as Summary;
  const labels = Object.entries(summary.split_labels ?? {}).sort(([a], [b]) => (
    (order.indexOf(a) + 1 || order.length + 1) - (order.indexOf(b) + 1 || order.length + 1)
  ));
  const any = Object.values(summary.splits ?? {})
    .flatMap(entries => models.map(([model]) => entries?.[model])).find(Boolean);
  return <div className="evaluation-results">
    {any && <p className="evaluation-note">Unless a set says otherwise, all three models ran on the same GPU ({any.gpu_name ?? '—'}), batched, same prompt and schema. Row F1 is the model&apos;s own answer. Cost at ${typeof any.price_per_hour === 'number' ? any.price_per_hour.toFixed(2) : '—'}/h.</p>}
    {labels.map(([split, label]) => {
      const entries = summary.splits?.[split];
      if (!entries) return null;
      const first = models.map(([model]) => entries[model]).find(Boolean);
      if (entries.status === 'empty_set' || !first) {
        return <article key={split}><h2>{label} · 0 pages</h2><p>No pages in this set.</p></article>;
      }
      return <article key={split}>
        <h2>{label} · {Number(first.pages).toLocaleString('en-US')} pages</h2>
        {summary.split_notes?.[split] && <p className="evaluation-note">{summary.split_notes[split]}</p>}
        <div className="table-scroll"><table>
          <thead><tr><th scope="col">Metric</th>{models.map(([id, name]) => <th scope="col" key={id}>{name}</th>)}</tr></thead>
          <tbody>{metrics.map(([key, label, format]) => {
            const values = models.map(([id]) => entries[id]?.[key]);
            const numbers = values.filter((n): n is number => typeof n === 'number' && Number.isFinite(n));
            // Rows that don't apply to this set (no statements: no reconciliation or red flags).
            if (!numbers.length) return null;
            const best = format === 'rate' ? Math.max(...numbers) : Math.min(...numbers);
            return <tr key={key}><th scope="row">{key === 'row_f1' || key === 'row_f1_checked' ? <Term name="row F1" label={label} /> : key === 'row_precision' ? <Term name="precision" label={label} /> : key === 'row_recall' ? <Term name="recall" label={label} /> : key === 'reconciliation' ? <Term name="reconciliation" label={label} /> : key === 'header_accuracy' ? <Term name="header fields" label={label} /> : key.startsWith('anomaly_') ? <Term name="red flags" label={label} /> : label}</th>{values.map((n, i) => {
              const valid = typeof n === 'number' && Number.isFinite(n);
              const text = !valid ? '—' : format === 'rate' ? `${(n * 100).toFixed(1)}%` : format === 'dollars' ? `$${n.toFixed(2)}` : n.toFixed(2);
              return <td key={models[i][0]}>{valid && n === best ? <strong>{text}</strong> : text}</td>;
            })}</tr>;
          })}</tbody>
        </table></div>
      </article>;
    })}
  </div>;
}

type HardPage = {id: string; row_f1: Record<string, number>; meta: Record<string, unknown> | null};
type Breakdown = {splits: Record<string, {models: Record<string, {by_strength: Record<string, {row_f1: number}>; by_layout_id: Record<string, {row_f1: number}>}>; hardest_pages: HardPage[]}>};
export function ScoreBars({values}: {values: Record<string, number | undefined>}) {
  return <div className="score-bars">{modelChoices.map(m => <div className={`score-bar model-${m.key}`} key={m.key}><span>{m.name}</span><span className="bar-track"><span style={{width:`${Math.max(0,Math.min(100,(values[scoreKey(m.key)] || 0)*100))}%`}} /></span><strong>{values[scoreKey(m.key)] == null ? '—' : `${(values[scoreKey(m.key)]! * 100).toFixed(1)}%`}</strong></div>)}</div>;
}
export function ResultsHeadline({summary}: {summary: Summary}) {
  const entries = summary.splits?.test_unseen_layout;
  const pages = modelChoices.map(m => entries?.[scoreKey(m.key)]?.pages).find(v => v != null);
  return <article className="headline"><h2>Unseen layouts · <Term name="row F1" label="Lines read right (row F1)" /></h2><p>{pages == null ? 'No test data available' : `${Number(pages).toLocaleString('en-US')} pages tested`} · New statement and invoice layouts held back from training.</p><ScoreBars values={Object.fromEntries(modelChoices.map(m => [scoreKey(m.key),entries?.[scoreKey(m.key)]?.row_f1 as number | undefined]))} /><p>Statements match date, money out and money in; invoice items match description, quantity, unit price and amount.</p></article>;
}
function GroupChart({breakdown, field, title}: {breakdown: Breakdown; field: 'by_strength' | 'by_layout_id'; title: string}) {
  const outputs = breakdown.splits.test_unseen_layout?.models || {};
  const groups = [...new Set(Object.values(outputs).flatMap(v => Object.keys(v[field] || {})))];
  return <article><h2>{title}</h2><p>Unseen layouts · <Term name="average per page" /></p>{groups.map(group => <section className="chart-group" key={group}><h3>{group.replaceAll('_', ' ')}</h3><ScoreBars values={Object.fromEntries(modelChoices.map(m => [scoreKey(m.key),outputs[scoreKey(m.key)]?.[field]?.[group]?.row_f1]))} /></section>)}</article>;
}
export function Results() {
  const [summary,setSummary] = useState<Summary | null>(null);
  const [breakdown,setBreakdown] = useState<Breakdown | null>(null);
  const [error,setError] = useState('');
  const [selected,setSelected] = useState<HardPage | null>(null);
  const [detail,setDetail] = useState<Example | null>(null);
  const [note,setNote] = useState('');
  useEffect(() => {void Promise.all([getJSON<Summary>('/api/eval'),getJSON<Breakdown>('/api/results/breakdown')]).then(([s,b]) => {setSummary(s);setBreakdown(b);}).catch(e => setError(e.message));}, []);
  useEffect(() => {setDetail(null);setNote('');if (!selected) return;let active = true;void getJSON<Example>(`/api/results/examples/${selected.id}`).then(v => {if(active) setDetail(v);}).catch(() => {if(active) setNote("The per-line outputs aren't bundled for this page.");});return () => {active = false;};}, [selected]);
  if (error) return <p role="alert" className="error">{error}</p>;
  if (!summary || !breakdown) return <p role="status">Loading recorded results…</p>;
  return <div className="page-stack"><ResultsHeadline summary={summary} /><ResultTable value={summary} /><article className="findings"><h2>What the test tells us</h2><p>Precision and recall are within about 1.5 points everywhere, so the models rarely invent or drop lines. Their mistakes are misread fields: money in versus money out, and digits.</p><p>The trained model finds 96% of planted red flags, but half of what it flags is not a planted flag (precision 51%). Treat a flag as a reason to investigate.</p></article><div className="two-tiles"><GroupChart breakdown={breakdown} field="by_strength" title="Lines read right, by photo damage" /><GroupChart breakdown={breakdown} field="by_layout_id" title="Lines read right, by layout" /></div><article><h2>Where it fails: the hardest pages</h2><div className="gallery">{Object.entries(breakdown.splits).flatMap(([split,b]) => b.hardest_pages.map(p => <section key={`${split}-${p.id}`}><h3>{p.id}</h3><p>{split.replaceAll('_',' ')} · {String(p.meta?.layout_id || 'layout not reported')} · {String(p.meta?.strength || 'damage not reported')}</p><ScoreBars values={p.row_f1} /><button aria-expanded={selected?.id === p.id} onClick={() => setSelected(p)}>Open page and lines →</button></section>))}</div></article>{selected && <article className="detail-panel"><button onClick={() => setSelected(null)}>Close detail</button><h2>{selected.id}</h2><p>Lines read right (row F1)</p><ScoreBars values={selected.row_f1} />{detail ? <LineTable example={detail} /> : <p>{note || 'Loading line outputs…'}</p>}</article>}<p>All three models graded in one batch on the same GPU (RTX PRO 4500, $0.72/h) with the same instructions.</p></div>;
}
