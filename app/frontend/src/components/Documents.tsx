import { Dispatch, SetStateAction } from 'react';
import { api, Page } from '../api';
import { Upload } from './MobileUpload';

type DocumentsProps = {
  session: string;
  refresh: () => Promise<void>;
  busy: boolean;
  pages: Page[];
  action: (fn: () => Promise<unknown>) => Promise<void>;
  model: string;
  types: Record<string, string>;
  setTypes: Dispatch<SetStateAction<Record<string, string>>>;
  setSelected: (page: string) => void;
  setRow: (row: number) => void;
  setTab: (tab: string) => void;
  setError: (error: string) => void;
};

export function Documents({
  session, refresh, busy, pages, action, model, types, setTypes,
  setSelected, setRow, setTab, setError,
}: DocumentsProps) {
  async function extractAll() {
    const failures: string[] = [];
    for (const page of pages) {
      try {
        await api.extract(session, page.id, model,
          types[page.id] || page.document?.doc_type || page.doc_type || 'statement');
      } catch (e) {
        failures.push(`${page.id}: ${(e as Error).message}`);
      }
      await refresh();
    }
    if (failures.length) throw new Error(failures.join("; "));
  }
  return (
    <>
      <div className="upload-grid">
        <Upload session={session} done={() => { void refresh().catch(e => setError((e as Error).message)); }} />
        <article>
          <h2>Use phone</h2>
          {session && <img className="qr" src={`/api/sessions/${session}/qr.png`}
            alt="Scan to upload from your phone" />}
          <p>Scan on the same Wi-Fi. Photos appear here automatically.</p>
        </article>
      </div>
      <div className="actions">
        <button disabled={busy || !session} onClick={() => void action(() => api.sample(session))}>
          Load sample business
        </button>
        <button disabled={busy || !pages.length} onClick={() => void action(extractAll)}>
          {busy ? 'Working…' : 'Read all pages'}
        </button>
      </div>
      <div className="pages">
        {pages.map(page => (
          <article key={page.id}>
            <img src={page.thumbnail_url} alt={`Page ${page.id}`} onClick={() => {
              setSelected(page.id);
              setRow(-1);
              setTab('Review');
            }} />
            <div>
              {page.id}
              <span className={`chip ${page.status === 'error' ? 'bad' : ''}`}>{page.status}</span>
            </div>
            {Boolean(page.metrics?.live_error) && (
              <details className="live-warning">
                <summary className="chip warning" title={String(page.metrics?.live_error)}>Live call failed — showing cached result</summary>
                <small>{String(page.metrics?.live_error)}</small>
              </details>
            )}
            {page.metrics?.source === 'cached-demo' && !page.metrics?.live_error && (
              <strong className="cached">Cached demo · ground truth</strong>
            )}
            <select aria-label={`Document type ${page.id}`}
              value={types[page.id] || page.document?.doc_type || page.doc_type || 'statement'}
              onChange={e => setTypes(value => ({ ...value, [page.id]: e.target.value }))}>
              <option value="statement">Statement</option>
              <option value="invoice">Invoice</option>
            </select>
            {page.error && <p className="error">{page.error}</p>}
          </article>
        ))}
      </div>
    </>
  );
}
