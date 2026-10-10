import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, Analysis, Citation, Page } from './api';
import { Upload } from './components/MobileUpload';
import { Documents } from './components/Documents';
import { Review } from './components/Review';
import { Cashflow } from './components/Analysis';
import { Memo } from './components/Memo';
import { models, modelState } from './models';
import { navigate, RouterLink, usePath } from './router';
import { UnderTheHood } from './components/UnderTheHood';
import { PageToolbar } from './components/PageToolbar';
import { Compare } from './components/Compare';
import { Results } from './components/Results';
import { Cost } from './components/Cost';
import { Data } from './components/Data';
import { Training, TrainingRun, PodAlert } from './components/Training';
import { getJSON } from './components/Compare';
import './style.css';

function App() {
  const path = usePath();
  const mobile = path.match(/^\/m\/([a-f0-9]{32})$/);
  const [session, setSession] = useState(
    mobile?.[1] || localStorage.getItem('ledgerlens-session') || '',
  );
  const [tab, setTab] = useState('Documents');
  const [pages, setPages] = useState<Page[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [selected, setSelected] = useState('');
  const [row, setRow] = useState(-1);
  const [model, setModel] = useState('ft');
  const [types, setTypes] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Record<string, unknown>>({});
  const [started, setStarted] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  async function refreshRuns() { setRuns(await getJSON<TrainingRun[]>('/api/training/runs')); }
  useEffect(() => { const poll = () => { void refreshRuns().catch(() => {}); }; poll(); const timer = setInterval(poll, 5000); return () => clearInterval(timer); }, []);
  const [presenter, setPresenter] = useState(false);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy, started]);

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

    return () => clearInterval(timer);
  }, [session]);

  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    setStarted(Date.now());
    setElapsed(0);
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
    if (path !== '/') navigate('/');
  }

  if (mobile) {
    return (
      <main className="mobile">
        <h1>LedgerLens</h1>
        <Upload session={session} mobile done={() => {}} />
      </main>
    );
  }
  const routes = [
    ['/', 'Read', 'Read statements', 'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6'],
    ['/compare', 'Compare', 'Compare models', 'M3 5h7v14H3z M14 5h7v14h-7z'],
    ['/data', 'Data', 'Explore data', 'M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0'],
    ['/training', 'Training', 'Training', 'M3 4v16h18 M6 8c3 0 3 8 6 8s3-3 7-3'],
    ['/results', 'Results', 'Results', 'M5 20V10 M12 20V4 M19 20v-7'],
    ['/cost', 'Cost', 'Cost calculator', 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18 M15 8h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9 M12 6v12'],
  ];
  function pageLabel(page: Page) {
    if (page.status === 'extracting') return 'reading';
    if (page.status === 'error') return 'error';
    if (!page.document) return 'not read';
    if (page.document.doc_type === 'invoice') return 'read';
    let balance = Number(page.document.opening_balance || 0);
    let problems = 0;
    for (const row of page.document.transactions || []) {
      const expected = balance + Number(row.credit || 0) - Number(row.debit || 0);
      if (row.balance == null || Math.abs(expected - Number(row.balance)) > 0.009) problems++;
      balance = row.balance == null ? expected : Number(row.balance);
    }
    return problems ? `${problems} to check` : 'read ✓';
  }
  const route = routes.find(r => r[0] === path);
  return (
    <div className="shell">
      <aside className="rail">
        <RouterLink href="/" className="brand" aria-label="LedgerLens home">LL</RouterLink>
        <nav aria-label="Workspace">
          {routes.map(([href, label, title, icon]) => <RouterLink key={href} href={href}
            aria-label={title} aria-current={path === href ? 'page' : undefined}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d={icon} /></svg><span>{label}</span>{href === "/training" && runs.some(r => r.status.startsWith("POD MAY STILL BE RUNNING")) && <span className="pod-badge" aria-label="Pod may still be running">!</span>}
          </RouterLink>)}
        </nav>
        <button className="presenter" aria-pressed={presenter} onClick={() => {setPresenter(!presenter); navigate('/'); setTab('Documents');}}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M7 4l13 8-13 8z" /></svg>Presenter
        </button>
      </aside>
      <main>
        <PodAlert runs={runs} />
        <header>
          <div><h1>{route?.[2] || 'Page not found'}</h1><p>{path === '/' ? 'Add photos of statements, check what was read, and fix any cell that is wrong.' : path === '/compare' ? 'See which lines each model reads right.' : path === '/results' ? 'Every score, including where the models fail.' : path === '/cost' ? 'Estimate the cost of reading your pages.' : path === '/data' ? 'Browse the practice documents the models learned from and were tested on.' : path === '/training' ? 'Replay the reported run or set up a new one. Nothing is rented until you confirm.' : 'Page not found.'}</p></div>
          <div className="model-status" aria-label="Model status">{models.map(m => <span key={m.key} className={modelState(status, m.key) === 'ready' ? 'ready' : ''}>{m.name} · {modelState(status, m.key)}</span>)}</div>
        </header>
        {error && <p role="alert" className="error">{error}</p>}
        {path === '/data' ? <Data /> : path === '/training' ? <Training runs={runs} refresh={refreshRuns} /> : path === '/compare' ? <Compare session={session} pages={pages} status={status} /> : path === '/results' ? <Results /> : path === '/cost' ? <Cost history={history} /> : path !== '/' ? <article className="placeholder"><h2>{route ? 'Coming next' : 'Page not found'}</h2><RouterLink href="/">Read statements →</RouterLink></article> : <>
        {presenter && <article className="tour">Three-minute tour: add pages (35s), check rows (40s), cash flow (45s), credit memo (35s), timing and cost (25s).</article>}
        <nav className="steps" aria-label="Read statements steps">{['Documents', 'Review', 'Analysis', 'Memo'].map((value, i) => <button key={value} aria-current={tab === value ? 'step' : undefined} onClick={() => setTab(value)}>{i + 1} {['Add pages', 'Check rows', 'Cash flow and red flags', 'Credit memo'][i]}</button>)}</nav>
        <div className="read-controls">
          {pages.length > 0 ? <PageToolbar pages={pages} selected={selected} busy={busy} label={pageLabel} choose={id => {setSelected(id); setRow(-1); setTab('Review');}} read={page => void action(() => api.extract(session, page.id, model, types[page.id] || page.document?.doc_type || page.doc_type || 'statement'))} /> : <Documents session={session} refresh={refresh} busy={busy} pages={pages} action={action} model={model} types={types} setTypes={setTypes} setSelected={setSelected} setRow={setRow} setTab={setTab} setError={setError} />}
          <article className="model-picker"><label htmlFor="model">Model reading the pages</label><select id="model" value={model} onChange={e => setModel(e.target.value)}>{models.map(m => <option key={m.key} value={m.key}>{m.name} · {modelState(status, m.key)}</option>)}</select><small>{models.find(m => m.key === model)?.technical}</small><button disabled={busy || !pages.length} onClick={() => void action(async () => {
            const failures: string[] = [];
            for (const p of pages) {
              try { await api.extract(session, p.id, model, types[p.id] || p.document?.doc_type || p.doc_type || 'statement'); }
              catch (e) { failures.push(`${p.id}: ${(e as Error).message}`); }
              await refresh();
            }
            if (failures.length) throw new Error(failures.join('; '));
            setTab('Review');
          })}>Read all pages</button></article>
        </div>
        {busy && <article role="status">Reading · {elapsed}s elapsed{(modelState(status, model) === 'waking up' || modelState(status, model) === 'asleep') && <p>Waiting for a GPU: the model was asleep. This can take a few minutes.</p>}</article>}
        {pages.length > 0 && tab === 'Documents' && <Documents session={session} refresh={refresh} busy={busy} pages={pages} action={action} model={model} types={types} setTypes={setTypes} setSelected={setSelected} setRow={setRow} setTab={setTab} setError={setError} />}
        {tab === 'Review' && (
          <Review busy={busy} read={page => void action(() => api.extract(session, page.id, model, types[page.id] || page.document?.doc_type || page.doc_type || "statement"))} pages={pages} selected={selected} row={row} analysis={analysis}
            choose={page => { setSelected(page); setRow(-1); }}
            save={async (page, index, field, value) => {
              await api.edit(session, page, index, field, value);
              await refresh();
            }} />
        )}
        {tab === 'Analysis' && <Cashflow data={analysis} jump={jump} />}
        <div hidden={tab !== 'Memo'}>
          <Memo status={status} session={session} model={model} jump={jump} />
        </div>
        <UnderTheHood history={history} status={status} />
        </>}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
