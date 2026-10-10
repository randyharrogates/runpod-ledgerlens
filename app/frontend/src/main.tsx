import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, Analysis, Citation, Page } from './api';
import { Upload } from './components/MobileUpload';
import { Documents } from './components/Documents';
import { Review } from './components/Review';
import { Cashflow } from './components/Analysis';
import { Memo } from './components/Memo';
import { ResultTable } from './components/Results';
import { UnderTheHood } from './components/UnderTheHood';
import './style.css';

function App() {
  const mobile = location.pathname.match(/^\/m\/([a-f0-9]{32})$/);
  const [session, setSession] = useState(
    mobile?.[1] || localStorage.getItem('ledgerlens-session') || '',
  );
  const [tab, setTab] = useState('Documents');
  const [pages, setPages] = useState<Page[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [selected, setSelected] = useState('');
  const [row, setRow] = useState(-1);
  const [model, setModel] = useState('8b');
  const [types, setTypes] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Record<string, unknown>>({});
  const [evaluation, setEvaluation] = useState<Record<string, unknown>>({ available: false });

  async function refresh() {
    if (!session) return;
    const result = await api.pages(session);
    setPages(result.pages);
    setHistory(result.requests);
    setAnalysis(await api.analysis(session));
  }

  useEffect(() => {
    if (!session) {
      void api.create().then(value => {
        localStorage.setItem('ledgerlens-session', value.session);
        setSession(value.session);
      }).catch(e => setError(e.message));
    }
  }, [session]);

  useEffect(() => {
    if (!session || mobile) return;
    const poll = () => {
      void refresh().catch(e => {
        // Sessions live in server memory: after a backend restart, start a fresh one.
        if (e.message === 'Session not found') {
          localStorage.removeItem('ledgerlens-session');
          setSession('');
          return;
        }
        setError(e.message);
      });
      void api.status().then(setStatus).catch(() => setStatus({ connection: 'unavailable' }));
    };
    poll();
    const timer = setInterval(poll, 3000);
    void api.evaluation().then(setEvaluation).catch(e => setError(e.message));
    return () => clearInterval(timer);
  }, [session]);

  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function jump(citation: Citation) {
    setSelected(citation.page_id);
    setRow(citation.row_index);
    setTab('Review');
  }

  if (mobile) {
    return (
      <main className="mobile">
        <h1>LedgerLens</h1>
        <Upload session={session} mobile done={() => {}} />
      </main>
    );
  }
  return (
    <div className="shell">
      <aside>
        <h1>LedgerLens</h1>
        <p>Evidence to lending insight</p>
        <nav>
          {['Documents', 'Review', 'Analysis', 'Memo', 'Results'].map(label => (
            <button className={tab === label ? 'active' : ''} key={label}
              onClick={() => setTab(label)}>
              {label}
            </button>
          ))}
        </nav>
        <small>Private session · fictional sample data</small>
      </aside>
      <main>
        <header>
          <div><small>SME CREDIT WORKSPACE</small><h1>{tab}</h1></div>
          <select aria-label="Extraction model" value={model}
            onChange={e => setModel(e.target.value)}>
            <option value="8b">Qwen3-VL-8B (base)</option>
            <option value="ft">Qwen3-VL-4B (fine-tuned)</option>
          </select>
        </header>
        {error && <p role="alert" className="error">{error}</p>}
        {tab === 'Documents' && (
          <Documents session={session} refresh={refresh} busy={busy} pages={pages}
            action={action} model={model} types={types} setTypes={setTypes}
            setSelected={setSelected} setRow={setRow} setTab={setTab} setError={setError} />
        )}
        {tab === 'Review' && (
          <Review pages={pages} selected={selected} row={row} analysis={analysis}
            choose={page => { setSelected(page); setRow(-1); }}
            save={async (page, index, field, value) => {
              await api.edit(session, page, index, field, value);
              await refresh();
            }} />
        )}
        {tab === 'Analysis' && <Cashflow data={analysis} jump={jump} />}
        <div hidden={tab !== 'Memo'}>
          <Memo session={session} model={model} jump={jump} />
        </div>
        {tab === 'Results' && (evaluation.available === false
          ? <p>Not run yet. Evaluation results will appear after eval/summary.json is generated.</p>
          : <ResultTable value={evaluation} />)}
        <UnderTheHood history={history} status={status} />
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
