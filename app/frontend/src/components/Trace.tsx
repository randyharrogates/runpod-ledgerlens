import { useId, useState } from 'react';
import { ChatEvent } from '../api';
import { LogEntry } from '../logs';
import { LogRows } from './Logs';
type Kind = 'router' | 'reader' | 'triage' | 'analyst' | 'writer' | 'checker' | 'tool' | 'data_agent' | 'other';
export type TraceSpan = {
  start: number;
  end: number;
  status: 'done' | 'error' | 'running' | 'stopped';
  detail: ChatEvent;
  rewrite: boolean;
};
export function traceModel(events: ChatEvent[]) {
  const anchor = events.find(e => typeof e.ts === 'number' || typeof e.rx === 'number');
  const origin = anchor?.ts ?? anchor?.rx ?? 0;
  const firstT = events.find(e => typeof e.t === 'number')?.t ?? 0;
  const time = (e: ChatEvent) => typeof e.ts === 'number' ? e.ts : typeof e.t === 'number' ? origin + e.t - firstT : e.rx ?? origin;
  const times = events.map(time);
  const workerTimes = events.filter(e => typeof e.ts === 'number').map(time);
  let start = workerTimes.length ? Math.min(...workerTimes) : Math.min(origin, ...times);
  let end = Math.max(start, ...times);
  const failed = events.some(e => e.type === 'error');
  const finished = events.some(e => ['final', 'error', 'metrics'].includes(e.type));
  const lanes: {
    id: string;
    label: string;
    kind: Kind;
    spans: TraceSpan[];
  }[] = [];
  const resets = new Set<string>();
  function lane(id: string, label: string, kind: Kind) {
    let l = lanes.find(l => l.id === id);
    if (!l) {
      l = {
        id,
        label,
        kind,
        spans: []
      };
      lanes.push(l);
    }
    return l;
  }
  for (const e of events) {
    if (e.type === 'section_reset') resets.add(e.section);
    if (e.type === 'step' || e.type === 'tool') {
      const kind: Kind = e.type === 'tool' ? 'tool' : ['router', 'reader', 'triage', 'analyst', 'writer', 'checker', 'data_agent'].includes(e.name) ? e.name : 'other';
      const id = [e.name, e.section, e.page_id].filter(Boolean).join(':');
      const name = e.name ? e.name[0].toUpperCase() + e.name.slice(1).replaceAll('_', ' ') : 'Tool';
      const label = [name, e.section?.replaceAll('_', ' '), e.page_id].filter(Boolean).join(' · ');
      const l = lane(id, label, kind);
      const t = time(e);
      const pending = [...l.spans].reverse().find(s => s.status === 'running');
      if (e.status !== 'start' && pending && e.type === 'step') {
        pending.end = typeof e.ts === 'number' || typeof e.t === 'number' || typeof e.rx === 'number' ? t : pending.start + (e.ms || 0) / 1000;
        pending.status = e.status === 'error' ? 'error' : e.status === 'stopped' ? 'stopped' : 'done';
        pending.detail = {
          ...pending.detail,
          ...e
        };
      } else {
        const duration = (e.ms || 0) / 1000;
        l.spans.push({
          start: e.status === 'start' ? t : t - duration,
          end: t,
          status: e.status === 'start' ? 'running' : e.status === 'error' || e.result?.error ? 'error' : 'done',
          detail: e,
          rewrite: resets.has(e.section)
        });
      }
    }
    if (e.type === 'section') {
      for (const l of lanes.filter(l => l.kind === 'checker')) for (const s of l.spans.filter(s => s.detail.section === e.section)) s.detail = {
        ...s.detail,
        problems: e.problems
      };
    }
  }
  const total = Number(events.find(e => e.type === 'metrics')?.total_s || 0);
  end = Math.max(end, start + total);
  const sections = ['summary', 'cash_flow', 'concentration', 'red_flags', 'questions'];
  const rank = (l: typeof lanes[number]) => {
    const section = sections.indexOf(l.spans[0]?.detail.section);
    return section < 0 ? -1 : section * 2 + (l.kind === 'checker' ? 1 : 0);
  };
  lanes.sort((a, b) => rank(a) - rank(b));
  end = Math.max(end, ...lanes.flatMap(l => l.spans.map(s => s.end)));
  start = Math.min(start, ...lanes.flatMap(l => l.spans.map(s => s.start)));
  for (const l of lanes) for (const s of l.spans) {
    if (s.status === 'running') {
      s.end = end;
      if (finished) s.status = 'stopped';
    }
    end = Math.max(end, s.end);
  }
  const delay = events.find(e => e.type === 'metrics')?.delayTime;
  if (typeof delay === 'number' && delay > 0) {
    const queued = start - delay / 1000;
    lanes.unshift({
      id: 'queue',
      label: 'Queue / cold start',
      kind: 'other',
      spans: [{
        start: queued,
        end: start,
        status: 'done',
        detail: {
          type: 'metrics',
          name: 'queue'
        },
        rewrite: false
      }]
    });
    start = queued;
  }
  const intent = events.find(e => e.type === 'route')?.intent || events.find(e => e.type === 'final')?.intent;
  // Columns follow the worker: pages are read first, the router and the analyst run side by
  // side, then writers (memo) or the data agent, then checkers or tool calls, then rewrites.
  function column(l: typeof lanes[number], span: TraceSpan) {
    if (l.id === 'queue') return 0;
    if (l.kind === 'triage' || l.kind === 'reader') return 1;
    if (l.kind === 'router' || l.kind === 'analyst') return 2;
    if (l.kind === 'data_agent') return 3;
    if (l.kind === 'tool') return 4;
    if (l.kind === 'writer') return span.rewrite ? 5 : 3;
    if (l.kind === 'checker') return intent === 'ask_data' ? 5 : 4;
    return 2;
  }
  const graphLanes = lanes.filter(l => {
    if (!intent || ['memo', 'edit_memo'].includes(intent) || l.id === 'queue') return true;
    return intent === 'ask_data'
      ? ['router', 'analyst', 'data_agent', 'tool', 'checker'].includes(l.kind)
      : l.kind === 'router';
  });
  const nodes = graphLanes.flatMap(l => l.spans.map((s, i) => ({
    id: `${l.id}-${i}`,
    label: l.label,
    kind: l.kind,
    column: column(l, s),
    span: s
  })));
  nodes.unshift({
    id: 'input',
    label: 'Input',
    kind: 'other',
    column: 0,
    span: {
      start,
      end: start,
      status: 'done',
      detail: {
        type: 'input',
        name: 'input'
      },
      rewrite: false
    }
  });
  nodes.push({
    id: 'answer',
    label: 'Answer',
    kind: 'other',
    column: 6,
    span: {
      start: end,
      end,
      status: failed ? 'error' : finished ? 'done' : 'running',
      detail: {
        type: 'final',
        name: 'answer'
      },
      rewrite: false
    }
  });
  const edges = nodes.flatMap(n => {
    const prior = nodes.filter(p => p.column < n.column);
    const col = Math.max(-1, ...prior.map(p => p.column));
    return prior.filter(p => {
      const sameSection = !n.span.detail.section || p.span.detail.section === n.span.detail.section;
      return p.column === col && (!(n.kind === 'checker' || n.span.rewrite) || sameSection);
    }).map(p => ({
      from: p.id,
      to: n.id
    }));
  });
  return {
    start,
    end,
    lanes,
    nodes,
    edges
  };
}
export function Trace({
  events,
  entries = [],
  turn = '',
  showLogs
}: {
  events: ChatEvent[];
  entries?: LogEntry[];
  turn?: string;
  showLogs: (name: string) => void;
}) {
  const arrowId = useId();
  const model = traceModel(events);
  const timelineNodes = model.lanes.flatMap(l => l.spans.map((span, i) => ({
    id: `${l.id}-${i}`,
    label: l.label,
    kind: l.kind,
    span
  })));
  const [selected, setSelected] = useState<string>('');
  const duration = Math.max(.01, model.end - model.start);
  const step = [0.5, 1, 2, 5].find(value => duration / value <= 12) || Math.ceil(duration / 50) * 5;
  const ticks = Array.from({
    length: Math.floor(duration / step) + 1
  }, (_, i) => i * step);
  if (ticks[ticks.length - 1] < duration) {
    // Drop a regular tick that would sit on top of the end label.
    if (duration - ticks[ticks.length - 1] < duration * 0.08) ticks.pop();
    ticks.push(duration);
  }
  const node = [...model.nodes, ...timelineNodes].find(n => n.id === selected);
  const selectedEntries = node ? entries.filter(entry =>
    entry.ts >= node.span.start - .5 && entry.ts <= node.span.end + .5
    && (!node.span.detail.page_id || entry.page_id === node.span.detail.page_id)
  ) : [];
  const desc = (s: TraceSpan, label: string) => `${label} · ${(s.end - s.start).toFixed(2)} s · ${s.status}${s.rewrite ? ' · rewrite' : ''} ${JSON.stringify({
    arguments: s.detail.arguments,
    problems: s.detail.problems || []
  })}`;
  function color(span: TraceSpan, kind: Kind) {
    if (span.status === 'error' || span.status === 'stopped') return 'var(--bad)';
    if (span.rewrite) return 'var(--orange)';
    if (span.status === 'running') return 'var(--sky)';
    return kind === 'tool' ? 'var(--violet)' : 'var(--good)';
  }
  function spanTime(seconds: number) {
    if (seconds < 0.001) return '<1 ms';
    return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(2)} s`;
  }
  function nodeCaption(span: TraceSpan, kind: Kind) {
    if (kind !== 'tool') {
      return `${spanTime(span.end - span.start)}${span.rewrite ? ' · rewrite' : ''}`;
    }
    const args = span.detail.arguments;
    return String(typeof args === 'string' ? args : JSON.stringify(args || {})).slice(0, 23);
  }
  const positions = new Map<string, {
    x: number;
    y: number;
  }>();
  const counts = new Map<number, number>();
  model.nodes.forEach(n => {
    const row = counts.get(n.column) || 0;
    counts.set(n.column, row + 1);
    positions.set(n.id, {
      x: n.column * 180 + 10,
      y: row * 75 + 45
    });
  });
  const height = Math.max(160, ...[...counts.values()].map(n => n * 75 + 60));
  // Scale the graph to the drawer; it never needs more width than its columns.
  const width = Math.max(...model.nodes.map(n => n.column), 0) * 180 + 175;
  return <div className="trace-view">
    {events.some(e => e.source === 'recorded') && <small>Recorded run · times as recorded</small>}
    <h3>Flow graph</h3>
    <div className="trace-scroll">
      <svg role="img" aria-label={`Turn trace: ${model.nodes.length} operations over ${duration.toFixed(1)} seconds`} viewBox={`0 0 ${width} ${height}`} style={{
        width: '100%',
        maxWidth: width,
        height: 'auto'
      }}>
        {model.edges.map((e, i) => {
          const a = positions.get(e.from)!,
            b = positions.get(e.to)!;
          return <path key={i} d={`M ${a.x + 155} ${a.y + 22} L ${b.x} ${b.y + 22}`} stroke="currentColor" opacity=".4" markerEnd={`url(#${arrowId})`} />;
        })}
        <defs>
          <marker id={arrowId} markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto">
            <path d="M0 0 L6 3 L0 6" fill="currentColor" />
          </marker>
        </defs>
        {model.nodes.map(n => {
          const p = positions.get(n.id)!;
          return <g key={n.id} data-node={n.id} role="button" tabIndex={0}
            aria-label={desc(n.span, n.label)}
            className={selected === n.id ? 'trace-selected' : ''}
            onClick={() => setSelected(n.id)} onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setSelected(n.id);
              }
            }}>
            <title>
              {desc(n.span, n.label)}
            </title>
            <rect x={p.x} y={p.y} width="155" height="55" rx="7" fill="var(--tile)" stroke={color(n.span, n.kind)} />
            <text x={p.x + 7} y={p.y + 19}>
              {n.label.slice(0, 24)}
            </text>
            <text x={p.x + 7} y={p.y + 40}>
              {nodeCaption(n.span, n.kind)}
            </text>
          </g>;
        })}
      </svg>
    </div>
    <h3>Timeline</h3>
    <div className="trace-scroll">
      <div className="trace-timeline">
        <div className="trace-axis">
          {ticks.map(tick => <span key={tick} style={{
            left: `${tick / duration * 100}%`
          }}>
            {Number(tick.toFixed(1))}
            s
          </span>)}
        </div>
        {model.lanes.map(l => <div className="trace-row" key={l.id}>
          <span>
            {l.label}
          </span>
          <div className="trace-track">
            {timelineNodes.filter(n => n.label === l.label).map(n => <button key={n.id}
              className={selected === n.id ? 'trace-selected' : ''}
              title={desc(n.span, n.label)} aria-label={desc(n.span, n.label)} style={{
                left: `min(calc(100% - 6px), ${(n.span.start - model.start) / duration * 100}%)`,
                width: `max(6px, ${(n.span.end - n.span.start) / duration * 100}%)`,
                maxWidth: `calc(100% - ${(n.span.start - model.start) / duration * 100}% + 6px)`,
                background: color(n.span, n.kind)
              }} onClick={() => setSelected(n.id)} />)}
          </div>
        </div>)}
      </div>
    </div>
    {node && <div>
      <h4>
        {node.label}
      </h4>
      <p>
        {desc(node.span, node.label)}
      </p>
      <button className="secondary" onClick={() => showLogs(node.span.detail.name || '')}>Show in Logs</button>
      <LogRows emptyText="No log lines in this window" entries={selectedEntries} filter={{
        source: '',
        errorsOnly: false,
        text: '',
        session: '',
        turn
      }} />
    </div>}
  </div>;
}
