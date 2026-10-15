import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, GpuStatus } from '../api';

export function gpuLabel(status: GpuStatus | null, now: number) {
  if (!status?.available || !status.workers_min) return 'GPU off';
  if (!status.warm_until) return 'GPU on (set elsewhere)';
  const workers = status.workers || {};
  if (!(workers.idle || workers.ready || workers.running)) return 'GPU starting';
  return `GPU ready · ${Math.max(0, Math.ceil((status.warm_until - now) / 60))} min`;
}

export function GpuPopover({ status, busy, error, change, close }: {
  status: GpuStatus | null; busy: boolean; error: string;
  change: (on: boolean, minutes?: number) => void; close: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { panel.current?.focus(); }, []);
  const low = (status?.cost_per_hour_low ?? 0.69).toFixed(2);
  const high = (status?.cost_per_hour_high ?? 1.58).toFixed(2);
  return <div className="gpu-popover" role="dialog" aria-label="Keep a GPU on" tabIndex={-1} ref={panel} onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } }}>
    <button className="gpu-close" aria-label="Close GPU settings" onClick={close}>×</button>
    <h3>Keep a GPU on</h3>
    <p>Keeps one GPU worker running so pages are read without the ~3 minute start. Billed while on: about ${low}–${high} per hour (flex list price; depends on the GPU).</p>
    {status?.warm_until && <p>Turns off automatically when the timer ends.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <p>{status?.workers_min ? 'Reset the timer to:' : 'Keep one GPU on for:'}</p>
    <div className="gpu-actions">
      <button disabled={busy} onClick={() => change(true, 30)}>30 min</button>
      <button disabled={busy} onClick={() => change(true, 60)}>1 hour</button>
      <button disabled={busy} onClick={() => change(true, 120)}>2 hours</button>
      {!!status?.workers_min && <button className="secondary" disabled={busy} onClick={() => change(false)}>Turn off now</button>}
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
  const starting = label === 'GPU starting';
  useEffect(() => {
    let active = true;
    const poll = () => { void api.gpu().then(value => { if (active) setStatus(value); }).catch(e => { if (active) setError(e.message); }); };
    poll();
    const timer = setInterval(poll, starting ? 3000 : 10000);
    return () => { active = false; clearInterval(timer); };
  }, [starting]);
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
  async function change(on: boolean, minutes = 30) {
    setBusy(true); setError('');
    try { setStatus(await api.setGpu(on, minutes)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="gpu-control">
    <button ref={trigger} className="presenter gpu-switch" disabled={!status?.available} title={status?.reason || error || 'Keep a GPU on'} aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
      <span className={`gpu-dot ${starting ? 'starting' : status?.workers_min ? 'ready' : ''}`} aria-hidden="true" />{label}
    </button>
    {status && !status.available && <small className="gpu-reason">{status.reason}</small>}
    {open && createPortal(<GpuPopover status={status} busy={busy} error={error} change={(on, minutes) => void change(on, minutes)} close={close} />, document.body)}
  </div>;
}
