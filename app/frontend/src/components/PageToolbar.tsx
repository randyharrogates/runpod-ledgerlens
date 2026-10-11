import { downloadCSV } from './export';
import { Page } from '../api';
export function PageToolbar({pages, selected, choose, read, busy, label}: {pages: Page[]; selected: string; choose: (id: string) => void; read: (page: Page) => void; busy: boolean; label: (page: Page) => string}) {
  const page = pages.find(p => p.id === selected) || pages[0];
  if (!page) return null;
  const rows = page.document?.transactions || page.document?.line_items || [];
  function download() {
    const fields = page.document?.doc_type === 'statement' ? ['date', 'description', 'debit', 'credit', 'balance'] : ['description', 'quantity', 'unit_price', 'amount'];
    downloadCSV(`${page.id}.csv`, [fields, ...rows.map(r => fields.map(f => r[f]))]);
  }
  function downloadAll() {
    const fields = ['page_id','doc_type','date','description','debit','credit','balance','quantity','unit_price','amount'];
    downloadCSV('all-pages.csv', [fields, ...pages.flatMap(p => (p.document?.transactions || p.document?.line_items || []).map(row => [p.id,p.document?.doc_type,...fields.slice(2).map(f => row[f])]))]);
  }
  return <article className="page-switcher"><div className="actions">{pages.map(p => <button key={p.id} className={p.id === page.id ? 'current' : ''} onClick={() => choose(p.id)}>{p.id} · {label(p)}</button>)}</div><div className="actions"><button disabled={busy} onClick={() => read(page)}>Read this page</button><button disabled={!rows.length} onClick={download}>Download CSV</button><button disabled={!pages.some(p => (p.document?.transactions || p.document?.line_items || []).length)} onClick={downloadAll}>Download all pages (CSV)</button><span className="chip">{page.metrics?.source === 'cached-demo' ? 'Recorded answer (demo)' : String(page.metrics?.gpu || (page.metrics ? 'GPU not reported' : 'Not read yet'))}</span></div>{Boolean(page.metrics?.live_error) && <small className="error">{String(page.metrics?.live_error)}</small>}</article>;
}
