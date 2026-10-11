import { useEffect, useState } from 'react';
import { dollars } from './format';
import { models } from '../models';
import { getJSON, scoreKey } from './Compare';
import { Summary } from './Results';
// Source: docs/BUILD_LOG.md, measured 9 Oct on a warm RTX 5090 (USD / 1,000 pages).
export const LIVE_BIG_COST = {low: 6.0, high: 6.6, gpu: 'RTX 5090'};
export function monthlyCost(pages: number, perThousand: number): number {
  return Math.max(0,Number.isFinite(pages) ? pages : 0) * Number(perThousand.toFixed(2)) / 1000;
}
export const monthlyDollars = (value: number) => value >= 100 && Number.isInteger(value) ? `$${value.toLocaleString('en-US')}` : dollars(value);
export function liveCostText(pages: number): string {
  return `$${LIVE_BIG_COST.low.toFixed(2)} to $${LIVE_BIG_COST.high.toFixed(2)} per 1,000 pages warm on ${LIVE_BIG_COST.gpu}; ${monthlyDollars(monthlyCost(pages,LIVE_BIG_COST.low))} to ${monthlyDollars(monthlyCost(pages,LIVE_BIG_COST.high))} per month`;
}
// Live Serverless rates from the clean phone-set pages: all three models, one page at a time, same RTX 5090.
export function liveRateText(summary: Summary | null, key: string, pages: number): string | null {
  const entry = summary?.splits?.test_phone_clean?.[scoreKey(key)];
  const rate = entry?.cost_per_1000_pages;
  if (typeof rate !== 'number') return null;
  const gpu = String(entry?.gpu_name || 'GPU not reported').replace('NVIDIA GeForce ', '');
  return `${dollars(rate)} per 1,000 pages live on ${gpu} (test pages, one at a time); ${monthlyDollars(monthlyCost(pages,rate))} per month`;
}
export function sessionCosts(history: Record<string, unknown>[], model: string) {
  const measured = history.filter(r => r.model === model && r.task === 'extract' && r.source === 'runpod' && !r.error && r.cost != null && Number.isFinite(Number(r.cost)));
  const gpus = [...new Set(measured.map(r => String(r.gpu || 'GPU not reported')))];
  return measured.length ? {perPage: measured.reduce((sum,r) => sum + Number(r.cost),0)/measured.length,gpu:gpus.join(', ')} : null;
}
export function Cost({history}: {history: Record<string, unknown>[]}) {
  const [pages,setPages] = useState(50000);
  const [model,setModel] = useState('ft');
  const [summary,setSummary] = useState<Summary | null>(null);
  const [error,setError] = useState('');
  const [copied,setCopied] = useState('');
  useEffect(() => {void getJSON<Summary>('/api/eval').then(setSummary).catch(e => setError(e.message));}, []);
  const entries = summary?.splits?.test_unseen_layout;
  const rates = Object.fromEntries(models.map(m => [m.key,typeof entries?.[scoreKey(m.key)]?.cost_per_1000_pages === 'number' ? entries[scoreKey(m.key)].cost_per_1000_pages as number : null]));
  const rate = rates[model];
  const max = Math.max(...Object.values(rates).map(v => v || 0),0.001);
  const live = models.map(m => {const session = sessionCosts(history,m.key);return `${m.name}: ${session ? `this session: $${session.perPage.toFixed(5)} per page measured on ${session.gpu}` : liveRateText(summary,m.key,pages) ?? (m.key === '8b' ? liveCostText(pages) : 'not measured yet')}`;});
  async function copy() {
    try {await navigator.clipboard.writeText(`LedgerLens estimate: ${pages.toLocaleString('en-US')} pages/month; ${models.find(m => m.key === model)?.name}.\nBatch: ${rate == null ? 'not measured' : `${dollars(rate)} per 1,000 pages; ${dollars(monthlyCost(pages,rate))} per month`}. RTX PRO 4500, $0.72/h; unseen-layout batch test.\nLive Serverless:\n${live.join('\n')}\nIdle cost $0; waking up can take minutes. GPUs: Romania, EU-RO-1. In-region option not measured yet.`);setCopied('Estimate copied');}catch {setCopied('Could not copy. Clipboard access is unavailable.');}
  }
  return <div className="page-stack">{error && <p role="alert" className="error">{error}</p>}<div className="cost-top"><article><div className="two-inputs"><label>Pages per month<input type="text" inputMode="numeric" value={pages.toLocaleString('en-US')} onChange={e => setPages(Math.max(0,Math.floor(Number(e.target.value.replaceAll(',', '')) || 0)))} /></label><label>Model<select value={model} onChange={e => setModel(e.target.value)}>{models.map(m => <option value={m.key} key={m.key}>{m.name}</option>)}</select></label></div><h2>Batch on a rented GPU</h2><p>Measured on an RTX PRO 4500 at $0.72/h · unseen-layout test.</p>{models.map(m => <section key={m.key} className={`cost-row model-${m.key} ${model === m.key ? 'chosen' : ''}`}><div><strong>{m.name}</strong><span>{rates[m.key] == null ? 'not measured' : `${dollars(rates[m.key]!)} per 1,000 pages`}</span></div><div><span className="bar-track"><span style={{width:`${(rates[m.key] || 0)/max*100}%`}} /></span><strong>{rates[m.key] == null ? '—' : dollars(monthlyCost(pages,rates[m.key]!))} / month</strong></div></section>)}</article><div className="page-stack"><article className="estimate" data-tour="cost"><h2>Your estimate, batch reading</h2><strong>{rate == null ? '—' : dollars(monthlyCost(pages,rate))}</strong><p>a month for {pages.toLocaleString('en-US')} pages on the {models.find(m => m.key === model)?.name.toLowerCase()}.</p><button onClick={() => void copy()}>Copy this estimate</button><p role="status">{copied}</p></article><article><h2>Where the GPUs are</h2><p>Romania, EU-RO-1. An in-region option is not measured yet.</p></article></div></div><div className="two-tiles"><article><h2>Live, page by page on Serverless</h2>{live.map(line => <p key={line}>{line}</p>)}<h3>Idle cost $0</h3><p>Workers scale to zero. Waking up can take minutes.</p></article><article><h2>Why batch is cheaper</h2><p>One rented GPU stays busy reading page after page, spreading the cost across the batch.</p><div className="two-tiles"><section><h3>Live</h3><p>Instant answers while you work, after the GPU wakes up. Higher cost per page.</p></section><section><h3>Batch</h3><p>Overnight jobs. Lower cost per page.</p></section></div></article></div></div>;
}
