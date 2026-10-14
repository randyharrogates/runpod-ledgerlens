import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, Analysis, Citation, Page } from './api';
import { Upload } from './components/MobileUpload';
import { Documents } from './components/Documents';
import { Review } from './components/Review';
const Cashflow = lazy(() => import('./components/Analysis').then(m => ({default: m.Cashflow})));
import { Memo } from './components/Memo';
import { models, modelState, statusSummary } from './models';
import { navigate, RouterLink, usePath } from './router';
import { UnderTheHood } from './components/UnderTheHood';
import { PageToolbar } from './components/PageToolbar';
const Compare = lazy(() => import('./components/Compare').then(m => ({default: m.Compare})));
const Results = lazy(() => import('./components/Results').then(m => ({default: m.Results})));
const Cost = lazy(() => import('./components/Cost').then(m => ({default: m.Cost})));
const Data = lazy(() => import('./components/Data').then(m => ({default: m.Data})));
const Training = lazy(() => import('./components/Training').then(m => ({default: m.Training})));
import { getJSON } from './request';
import { TrainingRun, PodAlert } from './components/PodAlert';
import { Presenter } from './components/Presenter';
import { Assistant } from './components/Assistant';
import { tour } from './tour';
import { installBrowserLogging } from './logs';
import { LogsDrawer } from './components/Logs';
import './style.css';

installBrowserLogging(() => {
  try { return localStorage.getItem('ledgerlens-session') || ''; } catch { return ''; }
});

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
  const [logsOpen, setLogsOpen] = useState(false);
  const [logsInitial, setLogsInitial] = useState<{turn?: string; jobId?: string}>({});
  const closeLogs = useCallback(() => setLogsOpen(false), []);
  useEffect(() => {
    const show = (event: Event) => { setLogsInitial((event as CustomEvent).detail || {}); setLogsOpen(true); };
    window.addEventListener('ledgerlens:logs', show);
    return () => window.removeEventListener('ledgerlens:logs', show);
  }, []);
  const [presenter, setPresenter] = useState(false);
  const [tourIndex, setTourIndex] = useState(0);
  function tourGo(index: number) { setTourIndex(index); navigate(tour[index].route + ((tour[index] as {query?: string}).query || '')); if (tour[index].readStep) setTab(['Documents','Review','Analysis','Memo'][tour[index].readStep! - 1]); }

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
    ['/assistant', 'Assistant', 'Credit assistant', 'M4 4h16v12H9l-5 4z M8 8h8 M8 12h5'],
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
    <div className={presenter ? "shell presenting" : "shell"} data-tour-active={presenter ? tour[tourIndex].highlight : undefined}>
      <aside className="rail">
        <RouterLink href="/" className="brand" aria-label="LedgerLens home">LL</RouterLink>
        <nav aria-label="Workspace">
          {routes.map(([href, label, title, icon]) => <RouterLink key={href} href={href}
            aria-label={title} aria-current={path === href ? 'page' : undefined}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d={icon} /></svg><span>{label}</span>{href === "/training" && runs.some(r => r.status.startsWith("POD MAY STILL BE RUNNING")) && <span className="pod-badge" aria-label="Pod may still be running">!</span>}
          </RouterLink>)}
        </nav>
        <button className="presenter" aria-pressed={presenter} onClick={() => {setPresenter(!presenter); tourGo(0);}}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M7 4l13 8-13 8z" /></svg>Presenter
        </button>
        <button className="presenter logs-toggle" aria-pressed={logsOpen} onClick={() => {setLogsInitial({}); setLogsOpen(!logsOpen);}}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M5 6h14 M5 12h14 M5 18h9" /></svg>Logs
        </button>
      </aside>
      <main>
        <PodAlert runs={runs} />
        <header>
          <div><h1>{route?.[2] || 'Page not found'}</h1><p>{path === '/' ? 'Add photos of statements, check what was read, and fix any cell that is wrong.' : path === '/compare' ? 'See which lines each model reads right.' : path === '/results' ? 'Every score, including where the models fail.' : path === '/cost' ? 'Estimate the cost of reading your pages.' : path === '/data' ? 'Browse the practice documents the models learned from and were tested on.' : path === '/assistant' ? 'Ask for a credit memo or about the documents. Pages are read as soon as you attach them.' : path === '/training' ? 'Replay the reported run or set up a new one. Nothing is rented until you confirm.' : 'Page not found.'}</p></div>
          <div className="model-status" aria-label="Model status">{statusSummary(status) ? <span className={modelState(status, 'ft') === 'ready' ? 'ready' : ''}>{statusSummary(status)}</span> : models.map(m => <span key={m.key} className={modelState(status, m.key) === 'ready' ? 'ready' : ''}>{m.name} · {modelState(status, m.key)}</span>)}</div>
        </header>
        {error && <p role="alert" className="error">{error}</p>}
        <Suspense fallback={<p role="status">Loading page…</p>}>{path === '/assistant' ? <Assistant session={session} pages={pages} refresh={refresh} status={status} jump={jump} /> : path === '/data' ? <Data /> : path === '/training' ? <Training runs={runs} refresh={refreshRuns} /> : path === '/compare' ? <Compare session={session} pages={pages} status={status} /> : path === '/results' ? <Results /> : path === '/cost' ? <Cost history={history} /> : path !== '/' ? <article className="placeholder"><h2>{route ? 'Coming next' : 'Page not found'}</h2><RouterLink href="/">Read statements →</RouterLink></article> : <>
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
        </>}</Suspense>
      </main>
      {presenter && <Presenter index={tourIndex} go={tourGo} exit={() => setPresenter(false)} readMemo={() => setTab('Memo')} />}
      <LogsDrawer open={logsOpen} onClose={closeLogs} session={session} initial={logsInitial} />
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
