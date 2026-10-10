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
  session, refresh, busy, pages, action, types, setTypes,
  setSelected, setRow, setTab, setError,
}: DocumentsProps) {
  return (
    <>
      <article className="add-pages"><h2>Add pages</h2><div className="upload-grid">
        <Upload session={session} done={() => { void refresh().catch(e => setError((e as Error).message)); }} />
        <section>
          <h2>Use phone</h2>
          {session && <img className="qr" src={`/api/sessions/${session}/qr.png`}
            alt="Scan to upload from your phone" />}
          <p>Scan on the same Wi-Fi. Photos appear here automatically.</p>
        </section>
      </div>
      <div className="actions">
        <button disabled={busy || !session} onClick={() => void action(() => api.sample(session))}>
          Load sample business
        </button>
      </div></article>
      <div className="pages">
        {pages.map(page => (
          <article key={page.id}>
            <button className="page-thumbnail" aria-label={`Check rows from ${page.id}`} onClick={() => {
              setSelected(page.id);
              setRow(-1);
              setTab('Review');
            }}><img src={page.thumbnail_url} alt={`Page ${page.id}`} /></button>
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
              <strong className="cached">cached demo</strong>
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
