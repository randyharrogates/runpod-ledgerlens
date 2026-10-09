import { useEffect, useState } from 'react';
import { Analysis, Page } from '../api';
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

function Cell({ value, display, edited, directionTitle, numeric, label, save }: CellProps) {
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
  save: (page: string, row: number, field: string, value: string | null) => Promise<void>;
};

export function Review({ pages, selected, row, choose, analysis, save }: ReviewProps) {
  const [zoom, setZoom] = useState(1);
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
  const widths = invoice ? ['46%', '12%', '19%', '19%'] : ['16%', '32%', '16%', '16%', '16%'];
  const currency = page.document?.currency || 'SGD';
  const check = analysis?.reconciliation.find(c => c.page_id === page.id);
  let previousBalance = Number(page.document?.opening_balance || 0);

  return (
    <>
      <select aria-label="Review page" value={page.id} onChange={e => choose(e.target.value)}>
        {pages.map(p => <option key={p.id}>{p.id}</option>)}
      </select>
      {check && (
        <span className={`chip ${check.reconciles ? 'good' : 'bad'}`}>
          {check.reconciles
            ? 'Reconciles'
            : `Off by ${money(Math.abs(Number(check.difference)), currency, true)}`}
        </span>
      )}
      <div className="review">
        <div className="image-pane">
          <label>
            Zoom · {zoom === 1 ? 'Fit width' : `${Math.round(zoom * 100)}%`}
            <input type="range" min="1" max="3" step=".1" value={zoom}
              onChange={e => setZoom(Number(e.target.value))} />
          </label>
          <div className="image-scroll">
            <img src={page.thumbnail_url} alt={`Statement evidence ${page.id}`}
              style={{ width: `${zoom * 100}%` }} />
          </div>
        </div>
        <div className="table-scroll review-table">
          {fixes.length > 0 && (
            <p className="direction-note">
              {fixes.length} rows moved between debit and credit to match the printed balance.
            </p>
          )}
          <table>
            <colgroup>
              <col style={{ width: '4%' }} />
              {widths.map((width, i) => <col key={i} style={{ width }} />)}
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                {fields.map(field => (
                  <th key={field} className={numericFields.includes(field) ? 'numeric' : ''}>
                    {field.replaceAll('_', ' ')}
                  </th>
                ))}
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
                    className={`${bad ? 'bad' : ''} ${row === i ? 'selected' : ''}`}>
                    <td>{i + 1}</td>
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
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length && <p>Extract this page to review its rows.</p>}
        </div>
      </div>
    </>
  );
}
