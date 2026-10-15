import { useEffect, useRef, useState } from 'react';
import { api, ChatEvent, Citation, Page, postEvents } from '../api';
import { openLogs } from '../logs';
import { modelState } from '../models';
import { MemoContent } from './Memo';
import { MemoReport, ReportSectionData } from './MemoReport';
import { Upload } from './MobileUpload';

export const sections = [
  ['summary', 'Summary'], ['cash_flow', 'Cash flow'], ['concentration', 'Concentration'],
  ['red_flags', 'Red flags'], ['questions', 'Questions for the applicant'],
] as const;
const labels: Record<string, string> = {
  memo: 'Credit memo', ask_data: 'Data question', edit_memo: 'Memo edit',
  knowledge: 'General answer', needs_documents: 'Needs documents', off_topic: 'Off topic', demo: 'Demo mode', error: 'Error',
};
type Card = {text: string; changed: boolean; resetting: boolean; rewritten: boolean; problems: string[]};
export type TurnState = {
  events: ChatEvent[]; report?: import('../api').MemoReportData; steps: ChatEvent[]; route?: ChatEvent; answer: string; cards: Record<string, Card>;
  turnId?: string; jobId?: string; metrics?: ChatEvent; error?: string; notices: string[]; final?: ChatEvent;
};

const problemLabels: Record<string, string> = {
  unverified_figure: 'Figure not found in the analysis', bad_citation: 'Unknown row reference',
  too_long: 'Too long', missing_heading: 'Missing heading',
};
export function problemText(problem: unknown) {
  if (typeof problem === 'string') return problem;
  const value = (problem || {}) as {kind?: unknown; detail?: unknown};
  const kind = typeof value.kind === 'string' ? problemLabels[value.kind] || value.kind : 'Check failed';
  return value.detail != null ? `${kind}: ${String(value.detail)}` : kind;
}

function plainError(value: unknown) {
  if (typeof value !== 'string' || /Traceback|\n\s+(?:at |File )/.test(value)) return 'The request failed';
  return value.split('\n')[0].slice(0, 300);
}

export function reduceTurn(events: ChatEvent[], previous: Record<string, string> = {}): TurnState {
  const turn: TurnState = {events, steps: [], answer: '', cards: {}, notices: []};
  for (const [id] of sections) turn.cards[id] = {
    text: previous[id] || '', changed: false, resetting: false, rewritten: false, problems: [],
  };
  function card(id: string) { return turn.cards[id]; }
  for (const event of events) {
    if (event.type === 'step') {
      const index = turn.steps.findIndex(step => step.type === 'step' && step.name === event.name
        && step.page_id === event.page_id && step.section === event.section && step.status === 'start');
      if (index >= 0 && event.status !== 'start') turn.steps[index] = event;
      else turn.steps.push(event);
      if (event.name === 'writer' && card(event.section) && !card(event.section).changed) {
        card(event.section).changed = true;
        card(event.section).text = '';
      }
    }
    if (event.type === 'tool') turn.steps.push(event);
    if (event.type === 'report') turn.report = event as unknown as import('../api').MemoReportData;
    if (event.type === 'route') turn.route = event;
    if (event.type === 'notice') turn.notices.push(event.message);
    if (event.type === 'deltas') for (const part of event.parts || []) {
      if (part.part === 'answer') turn.answer += part.text;
      else if (card(part.part)) {
        const value = card(part.part);
        if (!value.changed) value.text = '';
        value.changed = true;
        value.text += part.text;
      }
    }
    if (event.type === 'section_reset') {
      if (event.section === 'answer') turn.answer = '';
      else if (card(event.section)) Object.assign(card(event.section), {text: '', changed: true, resetting: true});
    }
    if (event.type === 'section' && card(event.section)) Object.assign(card(event.section), {
      text: event.text, changed: true, resetting: false, rewritten: event.rewritten, problems: (event.problems || []).map(problemText),
    });
    if (event.type === 'error') turn.error = plainError(event.message);
    if (event.type === 'turn') turn.turnId = event.turn_id;
    if (event.type === 'job') turn.jobId = event.job_id;
    if (event.type === 'metrics') {
      turn.metrics = event;
      if (event.turn_id) turn.turnId = event.turn_id;
      if (event.job_id) turn.jobId = event.job_id;
    }
    if (event.type === 'final') {
      turn.final = event;
      if (event.report) turn.report = event.report;
      if (typeof event.answer === 'string') turn.answer = event.answer;
      if (event.memo) for (const [id] of sections) {
        if (typeof event.memo[id] === 'string') card(id).text = event.memo[id];
      }
    }
  }
  return turn;
}

function toolText(event: ChatEvent) {
  let argumentsValue = event.arguments || {};
  if (typeof argumentsValue === 'string') {
    try { argumentsValue = JSON.parse(argumentsValue); } catch { argumentsValue = {}; }
  }
  const args = Object.entries(argumentsValue).map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`).join(', ');
  const result = event.result;
  const count = Array.isArray(result) ? result.length : result?.count ?? (Array.isArray(result?.rows) ? result.rows.length : undefined);
  return `${event.name}(${args})${count !== undefined ? ` · ${count} results` : ''}`;
}

function sectionBody(text: string, heading: string) {
  const lines = text.split('\n');
  if (lines[0].replace(/^#+\s*|\*\*/g, '').trim().replace(/[.:]$/, '').toLowerCase() === heading.toLowerCase()) lines.shift();
  return lines.join('\n').trimStart();
}

function CopyButton({text, label}: {text: string; label: string}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch { /* Clipboard permissions may be unavailable. */ }
  }
  return <button className="assistant-copy secondary" aria-label={copied ? 'Copied' : label} onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</button>;
}

function thinkingText(step?: ChatEvent) {
  if (!step) return 'Thinking…';
  if (step.name === 'reader') return `Reading ${step.page_id || 'pages'}…`;
  if (step.name === 'writer') return `Writing ${sections.find(([id]) => id === step.section)?.[1] || 'answer'}…`;
  if (step.name === 'checker') return 'Checking figures…';
  return `${step.name}${step.page_id ? ` · ${step.page_id}` : ''}…`;
}

export function TurnView({turn, jump}: {turn: TurnState; jump: (citation: Citation) => void}) {
  const intent = turn.route?.intent || turn.final?.intent || (turn.error ? 'error' : undefined);
  const memo = intent === 'memo' || intent === 'edit_memo' || !!turn.final?.memo;
  const measured = turn.metrics;
  const seconds = (value: unknown) => typeof value === 'number' ? `${value.toFixed(1)} s` : '—';
  const finished = !!(turn.final || turn.metrics || turn.error);
  const accent = turn.error || intent === 'error' ? 'bad' : memo ? 'violet' : intent === 'ask_data' ? 'sky' : intent === 'knowledge' ? 'good' : 'orange';
  const running = [...turn.steps].reverse().find(step => step.status === 'start');
  const thinking = !finished && !turn.answer && !Object.values(turn.cards).some(card => card.changed && card.text);
  const elapsed = measured?.total_s ?? turn.steps.reduce((sum, step) => sum + (step.ms || 0) / 1000, 0);
  return <article className={`assistant-turn assistant-intent-${accent}`} aria-live="polite">
    <span className="assistant-avatar" aria-label="LedgerLens">LL</span>
    {turn.steps.length > 0 && <details className="assistant-step-trail" key={finished ? 'finished' : 'running'} open={!finished}>
      <summary>{turn.steps.length} steps · {seconds(elapsed)}</summary>
      {turn.steps.map((step, i) => <div className="assistant-step" key={i}>
        <span className={`assistant-dot assistant-dot-${step.status === 'error' || step.status === 'stopped' || (finished && step.status === 'start') ? 'bad' : step.status === 'start' ? 'running' : 'good'}`} />
        <span>{step.type === 'tool' ? <><span className="assistant-tool">tool</span> {toolText(step)}</> : <>{step.name}{step.page_id || step.section ? ` · ${step.page_id || step.section}` : ''}</>}</span>
        <small>{step.status === 'start' ? finished ? 'stopped' : 'running' : step.status === 'error' || step.status === 'stopped' ? 'stopped' : seconds((step.ms || 0) / 1000)}</small>
      </div>)}
    </details>}
    {thinking && <div className="assistant-thinking" role="status"><span className="assistant-typing" aria-label="Typing"><i /><i /><i /></span>{thinkingText(running)}</div>}
    {intent && <div className="assistant-route"><strong className="assistant-intent-pill">{labels[intent] || 'General answer'}</strong>{turn.route?.reason && <small><MemoContent text={turn.route.reason} jump={jump} /></small>}</div>}
    {turn.notices.map((text, i) => <div key={i}><MemoContent text={text} jump={jump} /></div>)}
    {memo ? <MemoReport report={turn.report} finished={finished} problems={turn.final?.problems?.length || Object.values(turn.cards).reduce((n,c)=>n+c.problems.length,0)} texts={sections.map(([id])=>turn.cards[id].text)}>{sections.map(([id, heading], index) => {
      const card = turn.cards[id];
      const unchanged = intent === 'edit_memo' && !card.changed;
      return <section key={id} className={unchanged ? 'report-section unchanged' : 'report-section'}>
        <span className="report-number">{index+1}</span><h3>{heading}</h3>{card.text && <CopyButton text={sectionBody(card.text, heading)} label={`Copy ${heading}`} />}{unchanged && <small>unchanged</small>}
        {card.resetting && <small className="assistant-rewriting">rewriting after a check</small>}{card.rewritten && <small className="assistant-rewritten">rewritten once</small>}
        <ReportSectionData id={id} report={turn.report} jump={jump}/><div className="memo"><MemoContent text={sectionBody(card.text, heading)} jump={jump} /></div>
        {card.problems.map((problem, i) => <div className="assistant-warning" key={i}><MemoContent text={problem} jump={jump} /></div>)}
      </section>;
    })}</MemoReport> : <div className="memo"><MemoContent text={turn.answer} jump={jump} /></div>}
    {finished && !memo && turn.answer && <CopyButton text={turn.answer} label="Copy answer" />}
    {turn.error && <p role="alert">{turn.error === 'Stopped' ? 'You stopped this answer. Your pages are saved.' : `The agent stopped: ${turn.error}. Your pages are saved; try again.`}</p>}
    {(finished || measured || turn.turnId) && <footer className="assistant-metrics">{measured && [
      typeof measured.total_s === 'number' && `${seconds(measured.total_s)} total`,
      typeof measured.delayTime === 'number' && `${seconds(measured.delayTime / 1000)} queue / cold start`,
      typeof measured.executionTime === 'number' && `${seconds(measured.executionTime / 1000)} execution`,
      measured.usage?.prompt_tokens != null && `${measured.usage.prompt_tokens} tokens in / ${measured.usage.completion_tokens ?? '—'} out`,
      measured.gpu,
      typeof measured.cost === 'number' && `$${measured.cost.toFixed(5)}`,
    ].filter(Boolean).join(' · ')}{measured?.source === 'recorded' && <span className="assistant-recorded">Recorded run</span>}{!measured && turn.jobId && `job ${turn.jobId}`}{measured && turn.jobId && ` · job ${turn.jobId}`}
      {turn.turnId && <>{(measured || turn.jobId) && ' · '}<button className="link-button" onClick={() => openLogs({turn: turn.turnId, jobId: turn.jobId})}>Logs for this turn</button></>}{finished && <> · <button className="link-button" onClick={() => openLogs({turn:turn.turnId,jobId:turn.jobId,tab:'trace',events:turn.events})}>Trace</button></>}</footer>}
  </article>;
}

type Suggestion = {text: string; needs_documents: boolean; needs_memo: boolean};
type Turn = {message: string; events: ChatEvent[]; previous: Record<string, string>};
const threads = new Map<string, Turn[]>();
const memos = new Map<string, Record<string, string>>();

export function Assistant({session, pages, refresh, status, jump}: {
  session: string; pages: Page[]; refresh: () => Promise<void>;
  status: Record<string, unknown>; jump: (citation: Citation) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>(() => threads.get(session) || []);
  const [message, setMessage] = useState('');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [pageEvents, setPageEvents] = useState<Record<string, ChatEvent>>({});
  const [error, setError] = useState('');
  const [streaming, setStreaming] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const reading = useRef(new Set<string>());
  const readControllers = useRef(new Set<AbortController>());
  const memo = useRef<Record<string, string>>(memos.get(session) || {});
  const thread = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const [latest, setLatest] = useState(false);
  function scrollLatest() {
    if (thread.current) thread.current.scrollTop = thread.current.scrollHeight;
    follow.current = true;
    setLatest(false);
  }

  function updateTurns(change: (current: Turn[]) => Turn[]) {
    const next = change(threads.get(session) || turns);
    threads.set(session, next);
    setTurns(next);
  }

  async function readNew() {
    if (!session) return;
    const result = await api.pages(session);
    await refresh();
    const ids = result.pages.filter(page => !page.document && page.status !== 'rejected'
      && page.status !== 'error' && page.status !== 'reading' && !reading.current.has(page.id)).map(page => page.id);
    ids.forEach(id => reading.current.add(id));
    try {
      for (let i = 0; i < ids.length; i += 8) {
        const controller = new AbortController();
        readControllers.current.add(controller);
        try {
          await postEvents(`/api/sessions/${session}/read`, {page_ids: ids.slice(i, i + 8)}, event => {
            if (event.page_id) setPageEvents(current => ({...current, [event.page_id]: event}));
            if (event.type === 'error') setError(plainError(event.message));
          }, controller.signal);
        } finally { readControllers.current.delete(controller); }
        await refresh();
      }
    } catch (e) { if ((e as Error).name !== 'AbortError') setError(plainError((e as Error).message)); }
    finally { ids.forEach(id => reading.current.delete(id)); }
  }

  useEffect(() => {
    if (!session) return;
    setTurns(threads.get(session) || []);
    memo.current = memos.get(session) || {};
    setError('');
    void fetch(`/api/sessions/${session}/warm`, {method: 'POST'}).catch(() => {});
    void fetch('/api/chat/suggestions').then(response => response.json()).then(setSuggestions).catch(() => {});
    return () => {
      abort.current?.abort();
      readControllers.current.forEach(controller => controller.abort());
    };
  }, [session]);
  useEffect(() => { void readNew().catch(e => setError(plainError(e.message))); }, [session, pages.map(page => `${page.id}:${page.status}`).join(',')]);
  useEffect(() => {
    if (follow.current) scrollLatest();
    else setLatest(true);
  }, [turns]);
  useEffect(() => {
    const node = textarea.current;
    if (node) { node.style.height = 'auto'; node.style.height = `${Math.min(node.scrollHeight, 156)}px`; }
  }, [message]);

  async function send(text = message) {
    if (!session || streaming || !text.trim() || text.length > 2000) return;
    const controller = new AbortController();
    abort.current = controller;
    follow.current = true;
    setStreaming(true);
    setMessage('');
    const index = turns.length;
    updateTurns(current => [...current, {message: text, events: [], previous: {...memo.current}}]);
    let finalMemo: Record<string, string> | undefined;
    function append(event: ChatEvent) {
      updateTurns(current => current.map((turn, i) => i === index ? {...turn, events: [...turn.events, event]} : turn));
      if (event.type === 'final' && event.memo) finalMemo = event.memo;
      if (event.type === 'metrics' && finalMemo) {
        memo.current = finalMemo;
        memos.set(session, finalMemo);
      }
      if (event.page_id) setPageEvents(current => ({...current, [event.page_id]: event}));
    }
    try {
      await postEvents(`/api/sessions/${session}/chat`, {message: text}, append, controller.signal);
    } catch (e) {
      append({type: 'error', message: (e as Error).name === 'AbortError' ? 'Stopped' : (e as Error).message});
    } finally {
      setStreaming(false);
      abort.current = null;
      await refresh().catch(() => {});
    }
  }

  function pageStatus(page: Page) {
    const event = pageEvents[page.id];
    if (page.document) return `${(page.document.transactions || page.document.line_items || []).length} rows ✓`;
    if (page.status === 'rejected' || (event?.type === 'triage' && !event.accepted)) return 'not a financial document';
    if (page.status === 'error' || (event?.type === 'rows' && !event.valid)) return "couldn't read";
    if (event?.type === 'rows' && event.valid) return `${(event.document.transactions || event.document.line_items || []).length} rows ✓`;
    if (event?.name === 'triage') return 'checking page';
    if (event?.name === 'reader' || page.status === 'reading') return 'reading';
    return 'queued';
  }
  const hasDocuments = pages.some(page => page.document || !['rejected', 'error'].includes(page.status));
  function suggestionChips(welcome = false) {
    return <div className={`assistant-suggestions${welcome ? ' assistant-welcome-suggestions' : ''}`}>{suggestions.map(prompt => {
      const reason = prompt.needs_memo && !Object.keys(memo.current).length ? 'Write a memo first' : prompt.needs_documents && !hasDocuments ? 'Attach pages first' : '';
      return <button className="secondary" key={prompt.text} title={reason || prompt.text} disabled={streaming || !session || !!reason} onClick={() => void send(prompt.text)}>{prompt.text}{reason && <small>{reason}</small>}</button>;
    })}</div>;
  }
  return <div className="assistant-layout">
    <div className="assistant-thread" data-tour="assistant">
      <div className="assistant-messages" ref={thread} onScroll={e => {
        const node = e.currentTarget;
        follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
        setLatest(!follow.current);
      }}>
        {turns.length === 0 && <article className="assistant-welcome"><h2>Hi — I'm the LedgerLens credit assistant.</h2><p>I turn your business documents into a cited credit memo and help you explore the figures.</p>{suggestionChips(true)}</article>}
        {turns.map((turn, i) => <div className="assistant-exchange" key={i}>
          <div className="user-bubble"><MemoContent text={turn.message} jump={jump} /></div>
          <TurnView turn={reduceTurn(turn.events, turn.previous)} jump={jump} />
          {reduceTurn(turn.events).error && <button className="assistant-retry secondary" disabled={streaming} onClick={() => void send(turn.message)}>Try again</button>}
        </div>)}
      </div>
      <article className="assistant-composer">
        {latest && <button className="assistant-latest" onClick={scrollLatest}>↓ Latest</button>}
        {turns.length > 0 && suggestionChips()}
        <label htmlFor="assistant-message">Ask the credit assistant</label>
        <textarea ref={textarea} id="assistant-message" placeholder="Ask for a credit memo, or about the documents…" value={message} maxLength={2000} rows={1} onChange={e => setMessage(e.target.value)} onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
        }} />
        <div className="assistant-composer-actions"><small>Enter to send · Shift+Enter for a new line</small>{message.length >= 1800 && <small>{message.length} / 2,000</small>}
          {streaming ? <button className="assistant-stop" onClick={() => abort.current?.abort()}>Stop</button> : <button disabled={!session || !message.trim()} onClick={() => void send()}><svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h16m-6-6 6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" /></svg> Send</button>}
        </div>
      </article>
    </div>
    <aside className="assistant-evidence">
      <article><h2>Evidence</h2><p>Agent · {modelState(status, 'agent')}</p>
        <div className="assistant-pages">{pages.map(page => <div className="assistant-page" key={page.id}>
          <img src={page.thumbnail_url} alt={`Attached page ${page.id}`} /><div><strong>{page.id}</strong><small>{pageStatus(page)}</small></div>
        </div>)}</div>
        {pages.length === 0 && <button disabled={!session} onClick={() => void api.sample(session).then(readNew).catch(e => setError(plainError(e.message)))}>Load sample business</button>}
        {error && <p role="alert">{error}</p>}
      </article>
      <Upload session={session} done={() => void readNew().catch(e => setError(plainError(e.message)))} />
    </aside>
  </div>;
}
