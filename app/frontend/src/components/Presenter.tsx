import { useEffect, useRef, useState } from 'react';
import { tour } from '../tour';
export function clockText(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
export function Presenter({index, go, exit, readMemo}: {index: number; go: (index: number) => void; exit: () => void; readMemo: () => void}) {
  const [elapsed, setElapsed] = useState(0), [collapsed, setCollapsed] = useState(false);
  const started = useRef(Date.now()), panel = useRef<HTMLElement>(null);
  const step = tour[index];
  useEffect(() => {panel.current?.focus(); const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 1000); return () => clearInterval(timer);}, []);
  return <section ref={panel} tabIndex={-1} aria-label="Presenter tour" className={`presenter-panel ${collapsed ? 'collapsed' : ''}`} onKeyDown={event => {
    if (event.key === 'ArrowRight' && index < tour.length - 1) {event.preventDefault(); go(index + 1);}
    if (event.key === 'ArrowLeft' && index > 0) {event.preventDefault(); go(index - 1);}
  }}><div className="presenter-meta"><strong>Step {index + 1} of {tour.length} · {step.title}</strong><span>{clockText(step.start)}–{clockText(step.end)}</span><strong className={elapsed > step.end ? 'orange' : ''} aria-label="Tour countdown">{clockText(Math.max(0, tour[tour.length - 1].end - elapsed))}</strong><button aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}>{collapsed ? 'Expand' : 'Collapse'}</button><button onClick={exit}>Exit</button></div>{!collapsed && <><p className="presenter-words">{step.words}</p><div className="actions"><button disabled={index === 0} onClick={() => go(index - 1)}>Back</button><button disabled={index === tour.length - 1} onClick={() => go(index + 1)}>Next</button>{step.readSteps.some(value => value === 4) && <button onClick={readMemo}>Open credit memo (Read step 4)</button>}</div></>}</section>;
}
