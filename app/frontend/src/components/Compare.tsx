import { useEffect, useState } from 'react';
import { Page, Row } from '../api';
import { models, modelState } from '../models';
import { printedDate, number, metadata } from './format';
import { Term } from './Term';
export const scoreKey = (key: string) => key === 'ft' ? '4b-ft' : key;
export type ModelOutput = {rows: Row[]; matched?: (boolean | null)[]; matched_indices?: (number | null)[]; row_f1?: number; status: string; error?: string; metrics?: Record<string, unknown>; source?: string};
export type Example = {id: string; currency?: string; doc_type: string; meta: Record<string, unknown>; gold_rows: Row[]; models: Record<string, ModelOutput>};
export { getJSON } from '../request';
import { getJSON } from '../request';
const n = (v: unknown) => Number(v || 0);
export function verdict(gold: Row, rows: Row[], matched?: boolean | null, invoice = false): string {
  if (matched === true) return '✓ right';
  if (matched == null) return 'Not scored';
  const candidate = invoice ? rows.find(r => r.description === gold.description) : rows.find(r => r.date === gold.date && ((n(gold.credit) > 0 && n(r.debit) === n(gold.credit)) || (n(gold.debit) > 0 && n(r.credit) === n(gold.debit))));
  if (!invoice && candidate && n(gold.credit) > 0 && n(candidate.debit) === n(gold.credit)) return 'wrong · money in read as money out';
  if (!invoice && candidate && n(gold.debit) > 0 && n(candidate.credit) === n(gold.debit)) return 'wrong · money out read as money in';
  const same = candidate || rows.find(r => invoice ? r.description === gold.description : r.date === gold.date && r.description === gold.description);
  if (!same) return rows.length === 0 ? 'wrong · missing line' : 'wrong · missing or misread line';
  if (invoice ? n(same.amount) !== n(gold.amount) || n(same.unit_price) !== n(gold.unit_price) : n(same.debit) !== n(gold.debit) || n(same.credit) !== n(gold.credit)) return 'wrong · wrong amount';
  return 'wrong · misread fields';
}
export function LineTable({example}: {example: Example}) {
  const invoice = example.doc_type !== 'statement';
  return <article><h2>Line by line, side by side</h2><p><Term name="row F1" label="Lines read right (row F1)" /> · {example.currency || 'Currency not reported'}</p><div className="table-scroll"><table className="compare-table" data-tour="compare"><thead><tr><th>Line</th><th>What the page says</th>{models.map(m => <th key={m.key}>{m.name}</th>)}</tr></thead><tbody>{example.gold_rows.map((gold, i) => <tr key={i}><td>{i + 1}</td><td>{invoice ? Object.entries(gold).map(([k,v]) => `${k.replaceAll('_',' ')}: ${['quantity','unit_price','amount'].includes(k) && v != null ? number(v) : v ?? '—'}`).join(' · ') : `${printedDate(gold.date).replace(/ \d{4}$/, '')} · ${gold.description}${Number(gold.debit) ? ` · out ${number(gold.debit)}` : ''}${Number(gold.credit) ? ` · in ${number(gold.credit)}` : ''}`}</td>{models.map(m => {const output = example.models[scoreKey(m.key)]; const flag = output?.matched?.[i]; return <td key={m.key} className={flag === true ? 'good' : flag === false ? 'orange' : ''}>{verdict(gold, output?.rows || [], flag, invoice)}</td>;})}</tr>)}</tbody></table></div>{!example.gold_rows.length && <p>No known answers for this page. The outputs below are ungraded.</p>}</article>;
}
export function Compare({session, pages, status}: {session: string; pages: Page[]; status: Record<string, unknown>}) {
  const [mode, setMode] = useState(pages.length ? 'live' : 'recorded');
  const [list, setList] = useState<{id: string}[]>([]);
  const [recorded, setRecorded] = useState('biz_0007_p006');
  const [pageId, setPageId] = useState(pages[0]?.id || '');
  const [example, setExample] = useState<Example | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {void getJSON<{examples: {id: string}[]; skipped: string[]}>('/api/results/examples').then(v => {setList(v.examples);v.skipped?.forEach(s => console.info(s));}).catch(e => setError(e.message));}, []);
  useEffect(() => {
    setExample(null); setError('');
    let active = true;
    if (mode === 'recorded') void getJSON<Example>(`/api/results/examples/${recorded}`).then(v => {if (active) setExample(v);}).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [mode, recorded, pageId]);
  useEffect(() => {if (!busy) return; const started = Date.now(); const timer = setInterval(() => setElapsed(Math.floor((Date.now()-started)/1000)), 1000);return () => clearInterval(timer);}, [busy]);
  async function run() {
    const page = pages.find(p => p.id === pageId) || pages[0]; if (!page) return;
    setBusy(true);setElapsed(0);setError('');setExample(null);
    try {
      const result = await getJSON<{models: Record<string, {document: Page['document']; metrics: Record<string, unknown> | null; status: string; error?: string; source?: string}>; rows: {gold_row: number; models: Record<string, boolean | null>}[]; truth?: Page['document']}>(`/api/sessions/${session}/pages/${page.id}/compare`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc_type:page.document?.doc_type || page.doc_type || 'statement'})});
      const truth = result.truth;
      setExample({id:page.id, doc_type:truth?.doc_type || page.doc_type || 'statement',currency:truth?.currency,meta:{},gold_rows:truth?.transactions || truth?.line_items || [],models:Object.fromEntries(Object.entries(result.models).map(([key,v]) => [scoreKey(key),{rows:v.document?.transactions || v.document?.line_items || [],matched:result.rows.map(r => v.source === 'cached-demo' ? true : r.models[key]),status:v.status,error:v.error,source:v.source,metrics:v.metrics || undefined}]))});
    } catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <div className="page-stack"><nav className="steps" aria-label="Comparison mode"><button disabled={busy} aria-current={mode === 'live' ? 'page' : undefined} onClick={() => setMode('live')}>Your pages</button><button disabled={busy} aria-current={mode === 'recorded' ? 'page' : undefined} onClick={() => setMode('recorded')}>Recorded test examples</button></nav><div className="two-tiles"><article><label>Page to test<select disabled={busy} value={mode === 'recorded' ? recorded : pageId || pages[0]?.id || ''} onChange={e => mode === 'recorded' ? setRecorded(e.target.value) : setPageId(e.target.value)}>{(mode === 'recorded' ? list : pages).map(p => <option key={p.id}>{p.id}</option>)}</select></label><p>{mode === 'recorded' ? 'Real outputs from the final batch test. Always available offline.' : 'Sample business pages have known answers. Uploaded pages are ungraded.'}</p>{mode === 'live' && <button disabled={busy || !pages.length} onClick={() => void run()}>Run on all 3 models</button>}{mode === 'live' && !pages.length && <p>Add pages on Read statements first.</p>}</article><article><h2>Model state right now</h2>{models.map(m => <p key={m.key}>{m.name} · {modelState(status,m.key)}</p>)}</article></div>{error && <p role="alert" className="error">{error}</p>}<div className="model-cards">{models.map(m => {const output = example?.models[scoreKey(m.key)];const cached = output?.source === 'cached-demo' || output?.metrics?.source === 'cached-demo';return <article key={m.key}><h2>{m.name}</h2>{busy ? <p role="status">{elapsed}s elapsed · Waiting for a GPU or reading. Waking up can take minutes; reading time is reported when the request finishes.</p> : output ? <><span className="chip">{cached ? 'cached, not live' : output.status}</span>{output.error && <p className="error">{output.error}</p>}{output.matched?.some(v => v != null) ? <h3>{output.matched.filter(v => v === true).length}/{example?.gold_rows.length} lines right{cached && ' · cached answer, not a live score'}</h3> : <p>No line score available</p>}{output.row_f1 != null && <p>Lines read right (row F1): {(output.row_f1 * 100).toFixed(1)}%</p>}{output.metrics && !cached && <p>Waiting for GPU: {output.metrics.delayTime == null ? 'not reported' : `${n(output.metrics.delayTime)/1000}s`} · Reading: {output.metrics.executionTime == null ? 'not reported' : `${n(output.metrics.executionTime)/1000}s`} · Cost: {output.metrics.cost == null ? 'not reported' : `$${n(output.metrics.cost).toFixed(5)}`} · GPU: {String(output.metrics.gpu || 'not reported')}</p>}</> : <p>Pick a page to compare.</p>}</article>;})}</div>{example && <><article><h2>{example.id}</h2><p>{metadata(example.meta,example.currency)}</p>{example.id === 'biz_0007_p006' && mode === 'recorded' && <p>Both untrained models read money in as money out on 7 of 18 lines. The trained small model matched every line.</p>}</article><LineTable example={example} />{!example.gold_rows.length && <div className="model-cards">{models.map(m => <article key={m.key}><h2>{m.name} · ungraded output</h2><pre>{JSON.stringify(example.models[scoreKey(m.key)]?.rows,null,2)}</pre></article>)}</div>}</>}<p>One page is a smoke test: GPUs aren&apos;t bit-for-bit identical between runs, so the same page can score differently. The 1,499-page test on the Results page is the evidence.</p></div>;
}
