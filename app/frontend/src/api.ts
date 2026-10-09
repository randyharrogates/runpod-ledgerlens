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
  monthly: Record<string, { inflow: string; outflow: string; net: string }>;
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

export const api = {
  create: () => request<{
    session: string;
    upload_url_for_phone: string;
    qr_png_url: string;
  }>('/api/sessions', { method: 'POST' }),
  pages: (session: string) => request<{
    pages: Page[];
    requests: Record<string, unknown>[];
  }>(`/api/sessions/${session}`),
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
  memo: (session: string, model: string) => new EventSource(
    `/api/sessions/${session}/memo?model=${model}`,
  ),
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
