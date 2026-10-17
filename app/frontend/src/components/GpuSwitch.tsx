import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, GpuStatus } from '../api';

export function gpuLabel(status: GpuStatus | null, now: number) {
  if (status?.available && status.scope === 'all' && status.endpoints?.some(e => e.workers_min)) {
    const count = status.endpoints.filter(e => e.state === 'ready').length;
    if (status.endpoints.some(e => e.state === 'problem')) return `GPU problem · ${count}/${status.endpoints.length} ready`;
    return count === status.endpoints.length ? `All GPUs ready · ${Math.max(0, Math.ceil(((status.warm_until ?? now) - now) / 60))} min` : `GPUs starting · ${count}/${status.endpoints.length} ready`;
  }
  if (status?.available && status.endpoints?.some(e => e.state === 'stopping')) return 'GPUs stopping · still billed';
  if (!status?.available || !status.workers_min) return 'GPU off';
  if (status.ready_error) return 'GPU problem';
  if (!status.warm_until) return 'GPU on (set elsewhere)';
  if (!status.ready) return 'GPU starting';
  return `GPU ready · ${Math.max(0, Math.ceil((status.warm_until - now) / 60))} min`;
}

export function GpuPopover({ status, busy, error, change, close }: {
  status: GpuStatus | null; busy: boolean; error: string;
  change: (on: boolean, minutes?: number, scope?: 'agent' | 'all') => void; close: () => void;
}) {
  const [scope, setScope] = useState<'agent' | 'all'>(status?.scope ?? 'agent');
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { panel.current?.focus(); }, []);
  const low = ((scope === 'all' ? status?.all_cost_per_hour_low : status?.cost_per_hour_low) ?? 0.69).toFixed(2);
  const high = ((scope === 'all' ? status?.all_cost_per_hour_high : status?.cost_per_hour_high) ?? 1.58).toFixed(2);
  return <div className="gpu-popover" role="dialog" aria-label="Keep a GPU on" tabIndex={-1} ref={panel} onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } }}>
    <button className="gpu-close" aria-label="Close GPU settings" onClick={close}>×</button>
    <h3>Keep a GPU on</h3>
    <ul className="gpu-endpoints" aria-label="Endpoint status">{status?.endpoints?.map(endpoint => <li key={endpoint.endpoint_id}>
      <span className={`gpu-dot ${endpoint.state}`} aria-hidden="true" /><span><strong>{endpoint.label}</strong> · {endpoint.state}</span>
      {endpoint.state === 'ready' && endpoint.last_probe && <small>health check {((endpoint.last_probe.delay_ms ?? 0) / 1000).toFixed(2)} s queue + {((endpoint.last_probe.execution_ms ?? 0) / 1000).toFixed(1)} s, checked {Math.max(0, Math.floor(Date.now() / 1000 - (endpoint.checked_at ?? Date.now() / 1000)))} s ago{endpoint.last_probe.gpu ? ` · ${endpoint.last_probe.gpu}` : ''}</small>}
      {endpoint.state === 'problem' && <small>{endpoint.ready_error}</small>}
    </li>)}</ul>
    <fieldset className="gpu-scope"><legend>Which GPUs</legend>
      <label><input type="radio" name="gpu-scope" checked={scope === 'agent'} onChange={() => setScope('agent')} />Assistant only</label>
      <label><input type="radio" name="gpu-scope" checked={scope === 'all'} onChange={() => setScope('all')} />All endpoints (reading, assistant, cover image)</label>
    </fieldset>
    <p>Keeps one GPU worker per endpoint running, so nothing waits for a GPU to start (3–5 minutes from cold). Billed while on: about ${low}–${high} per hour (flex list price; depends on the GPU).</p>
    {status?.warm_until && <p>Turns off automatically when the timer ends.</p>}
    {status?.scope !== 'all' && !!status?.workers_min && <p role="status">{status.ready && status.last_probe
      ? `Model loaded — answered a test job in ${((status.last_probe.delay_ms ?? 0) / 1000).toFixed(2)} s queue + ${((status.last_probe.execution_ms ?? 0) / 1000).toFixed(1)} s (checked ${Math.max(0, Math.floor(Date.now() / 1000 - (status.checked_at ?? Date.now() / 1000)))} s ago)`
      : 'Waiting for the model to load. A fresh worker takes about 3 minutes; a paused one resumes in seconds.'}</p>}
    {status?.scope !== 'all' && status?.ready_error && <p role="alert" className="error">{status.ready_error}</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <p>{status?.workers_min ? 'Reset the timer to:' : 'Keep one GPU on for:'}</p>
    <div className="gpu-actions">
      <button disabled={busy} onClick={() => change(true, 30, scope)}>30 min</button>
      <button disabled={busy} onClick={() => change(true, 60, scope)}>1 hour</button>
      <button disabled={busy} onClick={() => change(true, 120, scope)}>2 hours</button>
      {(status?.endpoints?.some(e => e.workers_min) ?? !!status?.workers_min) && <button className="secondary" disabled={busy} onClick={() => change(false)}>Turn off now</button>}
    </div>
  </div>;
}

export function GpuSwitch() {
  const [status, setStatus] = useState<GpuStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);
  const trigger = useRef<HTMLButtonElement>(null);
  const label = gpuLabel(status, now);
  const armed = status?.endpoints?.some(e => e.workers_min) ?? !!status?.workers_min;
  const proven = status?.scope === 'all' ? status.endpoints?.every(e => e.state === 'ready') : status?.ready;
  const problem = status?.scope === 'all' ? status.endpoints?.some(e => e.state === 'problem') : !!status?.ready_error;
  const starting = armed && !proven;
  useEffect(() => {
    let active = true;
    const poll = () => { void api.gpu().then(value => { if (active) setStatus(value); }).catch(e => { if (active) setError(e.message); }); };
    poll();
    const timer = setInterval(poll, 3000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, []);
  function close() { setOpen(false); trigger.current?.focus(); }
  useEffect(() => {
    if (!open) return;
    // Esc anywhere, or a click outside the popover and its button, closes it.
    function key(e: KeyboardEvent) { if (e.key === 'Escape') close(); }
    function click(e: MouseEvent) {
      const target = e.target as Node;
      if (!trigger.current?.contains(target) && !document.querySelector('.gpu-popover')?.contains(target)) setOpen(false);
    }
    document.addEventListener('keydown', key);
    document.addEventListener('mousedown', click);
    return () => { document.removeEventListener('keydown', key); document.removeEventListener('mousedown', click); };
  }, [open]);
  async function change(on: boolean, minutes = 30, scope: 'agent' | 'all' = 'agent') {
    setBusy(true); setError('');
    try { setStatus(await api.setGpu(on, minutes, scope)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="gpu-control">
    <button ref={trigger} className="presenter gpu-switch" disabled={!status?.available} title={status?.reason || error || 'Keep a GPU on'} aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
      <span className={`gpu-dot ${problem ? 'problem' : armed && proven ? 'ready' : starting ? 'starting' : status?.endpoints?.some(e => e.state === 'stopping') ? 'stopping' : ''}`} aria-hidden="true" />{label}
    </button>
    {status && !status.available && <small className="gpu-reason">{status.reason}</small>}
    {open && createPortal(<GpuPopover status={status} busy={busy} error={error} change={(on, minutes, scope) => void change(on, minutes, scope)} close={close} />, document.body)}
  </div>;
}
