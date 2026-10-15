import { reportBrowserLog } from './logs';

export type Row = Record<string, string | null>;
export type DirectionFix = {
  row: number;
  from: 'debit' | 'credit';
  to: 'debit' | 'credit';
  amount: string;
};
export type Page = {
  id: string;
  doc_type?: string;
  date_format?: string | null;
  thumbnail_url: string;
  status: string;
  phase?: 'queued' | 'submitted' | 'starting' | 'triage' | 'reading' | 'done' | 'rejected' | 'error';
  phase_since?: number;
  job_id?: string | null;
  document: {
    doc_type: string;
    currency: string;
    opening_balance: string;
    transactions?: Row[];
    line_items?: Row[];
  } | null;
  metrics: Record<string, unknown> | null;
  edited: string[];
  direction_fixes: DirectionFix[];
  error?: string;
};
export type Citation = { page_id: string; row_index: number };
export type Analysis = {
  currency: string;
  monthly: Record<string, { inflow: string; outflow: string; net: string; partial: boolean; days_observed: number }>;
  average_balance: { value: string | null };
  minimum_balance: { value: string | null };
  top_payer_concentration: { share: string };
  reconciliation: { page_id: string; reconciles: boolean; difference: string }[];
  findings: { type: string; description: string; citations: Citation[] }[];
};

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string'
      ? body.detail
      : `Request failed (${response.status})`);
  }
  return response.json();
}

const json = (method: string, body: unknown) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export type GpuStatus = {
  available: boolean; reason?: string; endpoint_id?: string; name?: string;
  workers_min?: number; workers_max?: number; workers?: Record<string, number>;
  warm_until?: number | null; cost_per_hour_low?: number; cost_per_hour_high?: number;
};

export const api = {
  gpu: () => request<GpuStatus>('/api/gpu'),
  setGpu: (on: boolean, minutes = 30) => request<GpuStatus>('/api/gpu', json('POST', {on, minutes})),
  create: () => request<{
    session: string;
    upload_url_for_phone: string;
    qr_png_url: string;
  }>('/api/sessions', { method: 'POST' }),
  pages: (session: string) => request<{
    pages: Page[];
    requests: Record<string, unknown>[];
  }>(`/api/sessions/${session}`),
  read: (session: string, page_ids: string[]) => request<{page_ids: string[]; read_id: string}>(`/api/sessions/${session}/read`, json('POST', {page_ids})),
  cancelRead: (session: string) => request(`/api/sessions/${session}/read/cancel`, {method: 'POST'}),
  sample: (session: string) => request(`/api/sessions/${session}/sample`, { method: 'POST' }),
  extract: (session: string, page: string, model: string, doc_type: string) => request<{
    document: Page['document'];
    metrics: Page['metrics'];
    direction_fixes: DirectionFix[];
  }>(
    `/api/sessions/${session}/pages/${page}/extract`, json('POST', { model, doc_type }),
  ),
  edit: (
    session: string, page: string, row: number, field: string, value: string | null,
  ) => request(
    `/api/sessions/${session}/pages/${page}/rows/${row}`, json('PATCH', { field, value }),
  ),
  analysis: (session: string) => request<Analysis>(`/api/sessions/${session}/analysis`),
  status: () => request<Record<string, unknown>>('/api/status'),
  evaluation: () => request<Record<string, unknown>>('/api/eval'),
  memo: (session: string, model: string) => {
    const stream = new EventSource(`/api/sessions/${session}/memo?model=${model}`);
    stream.addEventListener('error', () => reportBrowserLog('error', 'memo stream error'), {once: true});
    return stream;
  },
  upload: (session: string, files: File[], progress: (value: number) => void) => (
    new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const body = new FormData();
      files.forEach(file => body.append('files', file));
      xhr.open('POST', `/api/sessions/${session}/pages`);
      xhr.upload.onprogress = event => progress(event.lengthComputable
        ? Math.round(event.loaded / event.total * 100)
        : 0);
      xhr.onerror = () => reject(new Error('Connection lost. Retry your upload.'));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else {
          let message = 'Upload failed';
          try {
            message = JSON.parse(xhr.responseText).detail;
          } catch {
            // Keep the readable fallback when the server response is not JSON.
          }
          reject(new Error(message));
        }
      };
      xhr.send(body);
    })
  ),
};

export type ChatEvent = {
  type: string;
  ts?: number; rx?: number; t?: number;
  report?: MemoReportData;
  [key: string]: any;
};

export async function postEvents(
  url: string, body: unknown, onEvent: (event: ChatEvent) => void, signal?: AbortSignal,
) {
  const response = await fetch(url, { ...json('POST', body), signal });
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(typeof value.detail === 'string' ? value.detail : `Request failed (${response.status})`);
  }
  if (!response.body) throw new Error('The stream is unavailable');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let kind = 'message';
  let data: string[] = [];
  function line(value: string) {
    if (!value) {
      if (data.length) { const event = {...JSON.parse(data.join('\n')), type:kind}; if (typeof event.ts !== 'number') event.rx = Date.now()/1000; onEvent(event); }
      kind = 'message';
      data = [];
    } else if (value.startsWith('event:')) kind = value.slice(6).trim();
    else if (value.startsWith('data:')) data.push(value.slice(5).replace(/^ /, ''));
  }
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      let end: number;
      while ((end = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, end).replace(/\r$/, ''));
        buffer = buffer.slice(end + 1);
      }
      if (done) {
        if (buffer) line(buffer.replace(/\r$/, ''));
        line('');
        break;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export type MemoReportData = {
  title:string; subtitle:string; borrower:string; bank:string; account:string; currency:string;
  period:{start:string;end:string}; prepared:string; audience:string; length:string; language:string;
  evidence:{statements:number;invoices:number;page_ids:string[]};
  totals:Record<string,string>; balances:{average:{value:string;cites:string[]};minimum:{value:string;cites:string[]}};
  monthly:{month:string;inflow:string;outflow:string;net:string;partial:boolean}[];
  top_payer:{payer:string;amount:string;total_inflow:string;share_percent:string;cites:string[]}|null;
  findings:{type:string;label:string;description:string;amount:string|null;cites:string[]}[];
  reconciliation:{page_id:string;printed:string;expected:string;difference:string;ok:boolean}[];
};
