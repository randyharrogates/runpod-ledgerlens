import { useEffect, useState } from 'react';
import { Analysis, Page } from '../api';
import { Term } from './Term';
import { money, printedDate } from './format';

const numericFields = ['debit', 'credit', 'balance', 'unit_price', 'amount', 'quantity'];

type CellProps = {
  value: string | null;
  display: string;
  edited: boolean;
  directionTitle?: string;
  numeric: boolean;
  label: string;
  save: (value: string | null) => Promise<void>;
};

export function Cell({ value, display, edited, directionTitle, numeric, label, save }: CellProps) {
  const [draft, setDraft] = useState(value || '');
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setDraft(value || ''), [value]);

  async function finish() {
    if (draft !== (value || '')) {
      try {
        await save(draft || null);
        setError('');
      } catch (e) {
        setError((e as Error).message);
        return;
      }
    }
    setEditing(false);
  }

  return (
    <td className={`${edited ? 'edited' : ''} ${numeric ? 'numeric' : ''}`}>
      <span className="cell-content">
        {directionTitle && <span className="direction-marker" title={directionTitle}>↔</span>}
        {edited && <span className="edit-marker" title="corrected by analyst">●</span>}
        {editing ? (
          <textarea
            autoFocus
            aria-label={label}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onBlur={() => void finish()}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.blur();
              }
            }}
          />
        ) : (
          <button className="cell-value" aria-label={label} onClick={() => setEditing(true)}>
            {display || '—'}
          </button>
        )}
      </span>
      {error && <small className="error">{error}</small>}
    </td>
  );
}

type ReviewProps = {
  pages: Page[];
  selected: string;
  row: number;
  choose: (page: string) => void;
  analysis: Analysis | null;
  read?: (page: Page) => void;
  busy?: boolean;
  save: (page: string, row: number, field: string, value: string | null) => Promise<void>;
};

export function Review({ pages, selected, row, choose, analysis, save, read, busy }: ReviewProps) {
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState(-1);
  const page = pages.find(p => p.id === selected) || pages[0];
  useEffect(() => setZoom(1), [selected]);
  useEffect(() => {
    document.getElementById(`row-${row}`)?.scrollIntoView({ block: 'center' });
  }, [row, selected]);
  if (!page) return <p>Upload or load sample pages first.</p>;
  const rows = page.document?.transactions || page.document?.line_items || [];
  const fixes = page.direction_fixes || [];
  const invoice = page.document?.doc_type === 'invoice';
  const fields = invoice
    ? ['description', 'quantity', 'unit_price', 'amount']
    : ['date', 'description', 'debit', 'credit', 'balance'];
  const widths = invoice ? ['44%', '12%', '18%', '18%'] : ['13%', '27%', '14%', '14%', '16%'];
  const currency = page.document?.currency || 'SGD';
  const check = analysis?.reconciliation.find(c => c.page_id === page.id);
  const flagged = new Set(analysis?.findings.flatMap(f => f.citations.filter(c => c.page_id === page.id).map(c => c.row_index)) || []);
  let running = Number(page.document?.opening_balance || 0);
  const checks = rows.map(r => {
    const expected = running + Number(r.credit || 0) - Number(r.debit || 0);
    const ok = r.balance != null && Math.abs(expected - Number(r.balance)) <= 0.009;
    running = r.balance == null ? expected : Number(r.balance);
    return ok;
  });
  function download() {
    const quote = (value: unknown) => '"' + String(value ?? '').replaceAll('"', '""') + '"';
    const csv = [fields, ...rows.map(r => fields.map(f => r[f]))].map(r => r.map(quote).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `${page.id}.csv`; link.click(); URL.revokeObjectURL(url);
  }
  let previousBalance = Number(page.document?.opening_balance || 0);

  return (
    <>
      <select aria-label="Review page" value={page.id} onChange={e => choose(e.target.value)}>
        {pages.map(p => <option key={p.id}>{p.id}</option>)}
      </select>
      {check && (
        <span className={`chip ${check.reconciles ? 'good' : 'bad'}`}>
          {check.reconciles
            ? 'Balances add up ✓'
            : `Off by ${money(Math.abs(Number(check.difference)), currency, true)}`}
        </span>
      )}
      <div className="actions"><button disabled={busy || !read} onClick={() => read?.(page)}>Read {page.id}</button>
      <button disabled={!rows.length} onClick={download}>Download CSV</button></div>
      {page.error && <p role="alert" className="error">{page.error}</p>}
      {page.metrics?.source === 'cached-demo' && <p className="cached">{page.metrics.live_error ? 'Live call failed — showing cached result' : 'Cached demo · ground truth'}{Boolean(page.metrics.live_error) && <small> · {String(page.metrics.live_error)}</small>}</p>}
      <div className="review">
        <div className="image-pane">
          <label className="zoom-control">
            Zoom · {zoom === 1 ? 'Fit width' : `${Math.round(zoom * 100)}%`}
            <input type="range" min="1" max="3" step=".1" value={zoom}
              onChange={e => setZoom(Number(e.target.value))} />
          </label>
          <div className="image-scroll">
            <img src={page.thumbnail_url} alt={`Statement evidence ${page.id}`}
              style={{ width: `${zoom * 100}%` }} />
          </div>
          <small>{page.id} · photo evidence</small>
          <div className="photo-rows" aria-label="Find a row in the table">{rows.map((_, i) => <button key={i} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onMouseLeave={() => setHover(-1)} onClick={() => {setHover(i); document.getElementById(`row-${i}`)?.scrollIntoView({block: 'center'});}}>Row {i + 1}{flagged.has(i) ? ' ⚑' : ''}</button>)}</div>
        </div>
        <div className="review-right">
          <div className="kpis"><article><small>Lines read</small><h2>{rows.length}</h2></article><article><small>Balances that add up</small><h2 className="ready">{invoice ? 'Not applicable' : `${checks.filter(Boolean).length} / ${rows.length}`}</h2></article><article><small>Red flags</small><h2 className="orange">{flagged.size}</h2></article></div>
        <section className="table-tile"><h2>What was read from {page.id}</h2><p>Click a cell to correct it. Brought forward: <span className="numeric">{money(page.document?.opening_balance, currency)}</span></p><p><Term name="reconciliation" /></p>
        <div className="table-scroll review-table">
          {fixes.length > 0 && (
            <p className="direction-note">
              {fixes.length} rows moved between debit and credit to match the printed balance.
            </p>
          )}
          <table>
            <colgroup>
              <col style={{ width: '44px' }} />
              {widths.map((width, i) => <col key={i} style={{ width }} />)}
              {!invoice && <col style={{width: '32px'}} />}
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                {fields.map(field => (
                  <th key={field} className={numericFields.includes(field) ? 'numeric' : ''}>
                    {field.replaceAll('_', ' ')}
                  </th>
                ))}
                {!invoice && <th>Check</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((transaction, i) => {
                const expected = previousBalance
                  + Number(transaction.credit || 0) - Number(transaction.debit || 0);
                const bad = !invoice && transaction.balance != null
                  && Math.abs(expected - Number(transaction.balance)) > 0.009;
                previousBalance = transaction.balance == null
                  ? expected : Number(transaction.balance);
                return (
                  <tr id={`row-${i}`} key={i}
                    onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(-1)} className={`${bad ? 'bad' : ''} ${flagged.has(i) ? 'flagged' : ''} ${row === i || hover === i ? 'selected' : ''}`}>
                    <td>{i + 1}{flagged.has(i) && <span title="Red flag"> ⚑</span>}</td>
                    {fields.map(field => {
                      const fix = fixes.find(f => f.row === i && f.to === field);
                      const directionTitle = fix
                        ? `Moved from ${fix.from} to ${fix.to}: the balance went ${fix.to === 'credit' ? 'up' : 'down'} by this amount`
                        : undefined;
                      const numeric = numericFields.includes(field);
                      const display = field === 'date'
                        ? printedDate(transaction[field], page.date_format)
                        : numeric && field !== 'quantity'
                          ? money(transaction[field], currency)
                          : transaction[field] || '';
                      return (
                        <Cell key={field} value={transaction[field]} display={display}
                          directionTitle={directionTitle}
                          numeric={numeric} label={`Edit row ${i + 1} ${field}`}
                          edited={page.edited.includes(`${i}:${field}`)}
                          save={value => save(page.id, i, field, value)} />
                      );
                    })}
                    {!invoice && <td className={checks[i] ? "ready" : "error"} aria-label={checks[i] ? "Balance adds up" : "Balance needs checking"}>{checks[i] ? "✓" : "!"}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length && <p>Extract this page to review its rows.</p>}
        </div>
        </section></div>
      </div>
    </>
  );
}
