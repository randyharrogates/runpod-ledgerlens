export type LogEntry = {
  id: number; ts: number; source: 'app'|'browser'|'worker'|'vllm';
  level: 'debug'|'info'|'warning'|'error'; message: string;
  session?: string; job_id?: string; turn?: string; page_id?: string; task?: string;
};
export type LogFilter = {source: string; errorsOnly: boolean; text: string; session: string; turn: string};
type BrowserEntry = {level: LogEntry['level']; message: string; url?: string};

export function filterLogs(entries: LogEntry[], filter: LogFilter): LogEntry[] {
  const text = filter.text.toLowerCase();
  return entries.filter(entry => (!filter.source || entry.source === filter.source)
    && (!filter.errorsOnly || entry.level === 'warning' || entry.level === 'error')
    && (!filter.session || entry.session === filter.session)
    && (!filter.turn || entry.turn === filter.turn)
    && (!text || [entry.message, entry.job_id, entry.page_id, entry.task].some(value => value?.toLowerCase().includes(text))));
}

export function openLogs(detail: {turn?: string; jobId?: string; tab?: 'trace'|'logs'; events?: import('./api').ChatEvent[]} = {}) {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ledgerlens:logs', {detail}));
}

export class BrowserLogQueue {
  entries: BrowserEntry[] = [];
  dropped = 0;
  push(entry: BrowserEntry) {
    if (this.entries.length >= 100) { this.dropped++; return; }
    this.entries.push({...entry, message: entry.message.slice(0, 2000), ...(entry.url ? {url: entry.url.slice(0, 300)} : {})});
  }
  take(): BrowserEntry[] | null {
    if (!this.entries.length && !this.dropped) return null;
    const batch: BrowserEntry[] = [];
    if (this.dropped) {
      batch.push({level: 'warning', message: `${this.dropped} browser log lines dropped`});
      this.dropped = 0;
    }
    // Leave room for the session and envelope in the backend's 32 KiB body limit.
    let size = new TextEncoder().encode(JSON.stringify(batch)).length;
    while (this.entries.length && batch.length < 20) {
      const next = this.entries[0];
      const bytes = new TextEncoder().encode(JSON.stringify(next)).length + 1;
      if (size + bytes > 31000) break;
      batch.push(this.entries.shift()!);
      size += bytes;
    }
    return batch;
  }
}

const queue = new BrowserLogQueue();
let installed = false;
let schedule: (() => void) | undefined;
export function reportBrowserLog(level: BrowserEntry['level'], message: string, url?: string) {
  queue.push({level, message, url});
  schedule?.();
}
function reasonMessage(reason: unknown) {
  if (reason && typeof reason === 'object' && 'message' in reason && typeof reason.message === 'string') return reason.message;
  return String(reason);
}

export function installBrowserLogging(getSession: () => string) {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  const original = window.fetch.bind(window);
  let timer: ReturnType<typeof setTimeout> | undefined;
  function flush() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    let session = '';
    try { session = getSession(); } catch { /* Storage can be unavailable. */ }
    // The backend only accepts lines for a live session; keep them queued until there is one.
    if (!session) { if (queue.entries.length) timer = setTimeout(flush, 2000); return; }
    const entries = queue.take();
    if (!entries) return;
    void original('/api/logs/browser', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session, entries}), keepalive: true,
    }).catch(() => {});
    if (queue.entries.length) timer = setTimeout(flush, 2000);
  }
  schedule = () => {
    if (queue.entries.length >= 10) flush();
    else if (timer === undefined) timer = setTimeout(flush, 2000);
  };
  window.addEventListener('error', event => {
    const stack = event.error instanceof Error ? event.error.stack?.split('\n').slice(0, 3).join('\n') : '';
    reportBrowserLog('error', `${event.message} ${event.filename}:${event.lineno}${stack ? `\n${stack}` : ''}`);
  });
  window.addEventListener('unhandledrejection', event => reportBrowserLog('error', reasonMessage(event.reason)));
  window.fetch = async (input, init) => {
    let path = '', method = init?.method || (input instanceof Request ? input.method : 'GET');
    try {
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
      if (url.origin === window.location.origin && url.pathname.startsWith('/api/')
        && !/^\/api\/logs(?:\/|$)/.test(url.pathname)) path = url.pathname;
    } catch { /* Let fetch handle invalid URLs. */ }
    method = method.toUpperCase();
    try {
      const response = await original(input, init);
      if (path && response.status >= 400) reportBrowserLog('warning', `${method} ${path} → ${response.status}`);
      return response;
    } catch (error) {
      if (path && (error as Error)?.name !== 'AbortError') reportBrowserLog('error', `${method} ${path} failed: ${reasonMessage(error)}`);
      throw error;
    }
  };
  if (queue.entries.length) schedule();
}
