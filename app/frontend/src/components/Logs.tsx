import { useEffect, useRef, useState } from 'react';
import { Trace } from './Trace';
import { ChatEvent } from '../api';
import { filterLogs, LogEntry, LogFilter } from '../logs';

export function LogRows({entries, filter, emptyText = 'No log lines match these filters.'}: {entries: LogEntry[]; filter: LogFilter; emptyText?: string}) {
  const shown = filterLogs(entries, filter);
  return <>{shown.length ? shown.map(entry => <div className="log-row" data-level={entry.level} key={entry.id}>
    <div className="log-heading"><time>{new Date(entry.ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false})}</time>
      <span className={`log-source log-${entry.source}`}>{entry.source}</span>
      <span className={`log-level log-${entry.level}`}>{entry.level}</span></div>
    <div className="log-message">{entry.message}</div>
    {(entry.session || entry.job_id || entry.page_id) && <small>{[
      entry.session?.slice(0, 6), entry.job_id && `job ${entry.job_id}`, entry.page_id,
    ].filter(Boolean).join(' · ')}</small>}
  </div>) : <p>{emptyText}</p>}</>;
}

export function LogsDrawer({open, onClose, session, initial}: {
  open: boolean; onClose: () => void; session: string; initial?: {turn?: string; jobId?: string; tab?: 'trace'|'logs'; events?: ChatEvent[]};
}) {
  const [tab, setTab] = useState(initial?.tab || 'logs');
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [source, setSource] = useState('');
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [text, setText] = useState('');
  const [thisSession, setThisSession] = useState(true);
  const [turn, setTurn] = useState(initial?.turn || '');
  const [live, setLive] = useState(false);
  const [paused, setPaused] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) { setTurn(initial?.turn || ''); setTab(initial?.tab || 'logs'); } }, [open, initial]);
  useEffect(() => {
    if (!open || !session) return;
    let closed = false;
    const scope = `session=${encodeURIComponent(session)}`;
    let stream: EventSource | undefined;
    const controller = new AbortController();
    setEntries([]); setLive(false); setPaused(false);
    function connect(cursor: number) {
      if (closed) return;
      stream = new EventSource(`/api/logs/stream?${scope}&since=${cursor}`);
      stream.onopen = () => setLive(true);
      stream.onerror = () => setLive(false);
      stream.addEventListener('log', event => {
        try {
          const entry = JSON.parse((event as MessageEvent).data) as LogEntry;
          setEntries(current => current.some(line => line.id === entry.id) ? current : [...current, entry].slice(-2000));
        } catch { /* A malformed frame must not stop the stream. */ }
      });
    }
    void fetch(`/api/logs?${scope}&since=0&limit=1000`, {signal: controller.signal}).then(response => {
      if (!response.ok) throw new Error('Logs unavailable');
      return response.json();
    }).then((value: {entries: LogEntry[]; cursor: number}) => {
      if (closed) return;
      setEntries(value.entries.slice(-2000)); connect(value.cursor);
    }).catch(() => { if (!closed) setLive(false); });
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', escape);
    return () => { closed = true; controller.abort(); stream?.close(); window.removeEventListener('keydown', escape); };
  }, [open, onClose, session]);
  const filter = {source, errorsOnly, text, session: thisSession ? session : '', turn};
  const shown = filterLogs(entries, filter).length;
  useEffect(() => {
    if (open && !paused && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [open, entries, shown, paused]);
  if (!open) return null;
  return <aside className={tab === 'trace' ? 'logs-drawer logs-drawer-wide' : 'logs-drawer'} role="dialog" aria-label="Logs" aria-modal="false">
    <div className="logs-header"><h2>Logs</h2><small>{shown} of {entries.length} lines</small><button className="secondary" onClick={onClose}>Close</button></div>
    <div className="logs-tabs" role="tablist" aria-label="Turn details"><button role="tab" aria-selected={tab==='trace'} onClick={()=>setTab('trace')}>Trace</button><button role="tab" aria-selected={tab==='logs'} onClick={()=>setTab('logs')}>Logs</button></div>
    {tab==='trace' ? <Trace events={initial?.events || []} entries={entries} turn={initial?.turn} showLogs={name=>{setSource('');setErrorsOnly(false);setThisSession(true);setText(name);setTurn(initial?.turn || '');setTab('logs');}}/> : <><div className="logs-filters">
      <label>Source <select value={source} onChange={e => setSource(e.target.value)}>{[
        ['', 'All sources'], ['app', 'App'], ['browser', 'Browser'], ['worker', 'Worker'], ['vllm', 'vLLM'],
      ].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label><input type="checkbox" checked={errorsOnly} onChange={e => setErrorsOnly(e.target.checked)} /> Errors only</label>
      <input aria-label="Search logs" placeholder="Search logs" value={text} onChange={e => setText(e.target.value)} />
      <label><input type="checkbox" checked={thisSession} onChange={e => setThisSession(e.target.checked)} /> This session only</label>
      {turn && <button className="secondary logs-turn" onClick={() => setTurn('')}>This turn only{initial?.jobId ? ` · job ${initial.jobId}` : ''} ×</button>}
    </div>
    <div className="logs-status"><small><span className={`logs-status-dot ${live ? 'logs-live' : 'logs-reconnecting'}`} />{live ? 'Live' : 'Reconnecting…'}</small>{paused && <button className="link-button" onClick={() => {
      if (list.current) list.current.scrollTop = list.current.scrollHeight;
      setPaused(false);
    }}>Paused · Jump to latest</button>}</div>
    <div className="logs-list" ref={list} onScroll={e => {
      const node = e.currentTarget;
      setPaused(node.scrollHeight - node.scrollTop - node.clientHeight > 40);
    }}><LogRows entries={entries} filter={filter} /></div></> }
  </aside>;
}
