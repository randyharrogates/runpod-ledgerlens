import { useEffect, useRef, useState } from 'react';
import { api, ChatEvent, Citation, Page, postEvents } from '../api';
import { openLogs } from '../logs';
import { modelState } from '../models';
import { MemoContent } from './Memo';
import { Upload } from './MobileUpload';

export const sections = [
  ['summary', 'Summary'], ['cash_flow', 'Cash flow'], ['concentration', 'Concentration'],
  ['red_flags', 'Red flags'], ['questions', 'Questions for the applicant'],
] as const;
const labels: Record<string, string> = {
  memo: 'Credit memo', ask_data: 'Data question', edit_memo: 'Memo edit',
  knowledge: 'General answer', needs_documents: 'Needs documents', off_topic: 'Off topic', demo: 'Demo mode',
};
type Card = {text: string; changed: boolean; resetting: boolean; rewritten: boolean; problems: string[]};
export type TurnState = {
  steps: ChatEvent[]; route?: ChatEvent; answer: string; cards: Record<string, Card>;
  turnId?: string; jobId?: string; metrics?: ChatEvent; error?: string; notices: string[]; final?: ChatEvent;
};

function plainError(value: unknown) {
  if (typeof value !== 'string' || /Traceback|\n\s+(?:at |File )/.test(value)) return 'The request failed';
  return value.split('\n')[0].slice(0, 300);
}

export function reduceTurn(events: ChatEvent[], previous: Record<string, string> = {}): TurnState {
  const turn: TurnState = {steps: [], answer: '', cards: {}, notices: []};
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
      text: event.text, changed: true, resetting: false, rewritten: event.rewritten, problems: event.problems || [],
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
      if (event.answer !== undefined) turn.answer = event.answer;
      if (event.memo) for (const [id] of sections) {
        if (event.memo[id] !== undefined) card(id).text = event.memo[id];
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
  if (lines[0].replace(/^##\s+|\*\*/g, '').trim().toLowerCase() === heading.toLowerCase()) lines.shift();
  return lines.join('\n').trimStart();
}

export function TurnView({turn, jump}: {turn: TurnState; jump: (citation: Citation) => void}) {
  const intent = turn.route?.intent || turn.final?.intent;
  const memo = intent === 'memo' || intent === 'edit_memo' || !!turn.final?.memo;
  const measured = turn.metrics;
  const seconds = (value: unknown) => typeof value === 'number' ? `${value.toFixed(1)} s` : '—';
  return <article className="assistant-turn">
    <div className="step-trail" aria-live="polite">{turn.steps.map((step, i) => <div key={i}>
      {step.type === 'tool' ? toolText(step) : <>{step.name}{step.page_id || step.section ? ` · ${step.page_id || step.section}` : ''} · {step.status === 'start' ? <span className="assistant-spinner" aria-label="Working">◌</span> : step.status === 'error' ? 'stopped' : seconds((step.ms || 0) / 1000)}</>}
    </div>)}</div>
    {intent && <div className="assistant-route"><strong>{labels[intent] || 'General answer'}</strong>{turn.route?.reason && <small><MemoContent text={turn.route.reason} jump={jump} /></small>}</div>}
    {turn.notices.map((text, i) => <div key={i}><MemoContent text={text} jump={jump} /></div>)}
    {memo ? <div className="assistant-cards">{sections.map(([id, heading]) => {
      const card = turn.cards[id];
      const unchanged = intent === 'edit_memo' && !card.changed;
      return <section key={id} className={unchanged ? 'assistant-card unchanged' : 'assistant-card'}>
        <h3>{heading}</h3>{unchanged && <small>unchanged</small>}
        {card.resetting && <small>rewriting after a check</small>}{card.rewritten && <small>rewritten once</small>}
        <div className="memo"><MemoContent text={sectionBody(card.text, heading)} jump={jump} /></div>
        {card.problems.map((problem, i) => <div className="warning" key={i}><MemoContent text={problem} jump={jump} /></div>)}
      </section>;
    })}</div> : <div className="memo"><MemoContent text={turn.answer} jump={jump} /></div>}
    {turn.error && <p role="alert">The agent stopped: {turn.error}. Your pages are saved; try again.</p>}
    {(measured || turn.turnId) && <footer className="assistant-metrics">{measured && [
      typeof measured.total_s === 'number' && `${seconds(measured.total_s)} total`,
      typeof measured.delayTime === 'number' && `${seconds(measured.delayTime / 1000)} queue / cold start`,
      typeof measured.executionTime === 'number' && `${seconds(measured.executionTime / 1000)} execution`,
      measured.usage?.prompt_tokens != null && `${measured.usage.prompt_tokens} tokens in / ${measured.usage.completion_tokens ?? '—'} out`,
      measured.gpu,
      typeof measured.cost === 'number' && `$${measured.cost.toFixed(5)}`,
      measured.source === 'recorded' && 'Recorded run',
    ].filter(Boolean).join(' · ')}{!measured && turn.jobId && `job ${turn.jobId}`}{measured && turn.jobId && ` · job ${turn.jobId}`}
      {turn.turnId && <>{(measured || turn.jobId) && ' · '}<button className="link-button" onClick={() => openLogs({turn: turn.turnId, jobId: turn.jobId})}>Logs for this turn</button></>}</footer>}
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
  const bottom = useRef<HTMLDivElement>(null);

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
    void fetch(`/api/sessions/${session}/warm`, {method: 'POST'}).catch(() => {});
    void fetch('/api/chat/suggestions').then(response => response.json()).then(setSuggestions).catch(() => {});
    return () => {
      abort.current?.abort();
      readControllers.current.forEach(controller => controller.abort());
    };
  }, [session]);
  useEffect(() => { void readNew().catch(e => setError(plainError(e.message))); }, [session, pages.map(page => `${page.id}:${page.status}`).join(',')]);
  useEffect(() => { bottom.current?.scrollIntoView({block: 'nearest'}); }, [turns]);

  async function send(text = message) {
    if (!session || streaming || !text.trim() || text.length > 2000) return;
    const controller = new AbortController();
    abort.current = controller;
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
  return <div className="assistant-layout">
    <div className="assistant-thread" data-tour="assistant">
      {turns.length === 0 && <article><p>Attach the business's pages, then ask a question or request a credit memo.</p></article>}
      {turns.map((turn, i) => <div className="assistant-exchange" key={i}>
        <div className="user-bubble"><MemoContent text={turn.message} jump={jump} /></div>
        <TurnView turn={reduceTurn(turn.events, turn.previous)} jump={jump} />
      </div>)}
      <div ref={bottom} />
      <article className="assistant-composer">
        <label htmlFor="assistant-message">Ask the credit assistant</label>
        <textarea id="assistant-message" value={message} maxLength={2000} rows={3} onChange={e => setMessage(e.target.value)} onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
        }} />
        <div className="actions">{message.length >= 1800 && <small>{message.length} / 2,000</small>}
          {streaming ? <button onClick={() => abort.current?.abort()}>Stop</button> : <button disabled={!session || !message.trim()} onClick={() => void send()}>Send</button>}
        </div>
        <div className="assistant-suggestions">{suggestions.map(prompt => {
          const reason = prompt.needs_memo && !Object.keys(memo.current).length ? 'Write a memo first' : prompt.needs_documents && !hasDocuments ? 'Attach pages first' : '';
          return <button className="secondary" key={prompt.text} title={reason || prompt.text} disabled={streaming || !session || !!reason} onClick={() => void send(prompt.text)}>{prompt.text}</button>;
        })}</div>
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
