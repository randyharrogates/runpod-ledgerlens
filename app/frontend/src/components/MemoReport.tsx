import { ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Citation, MemoReportData } from '../api';
import { MemoContent } from './Memo';
export const reportFooter =
  'Not covered here: the loan request, financial statements, collateral, ' +
  'debt service coverage, and the approve/decline decision — these stay with the credit officer.';
export function reportMarkdown(r: MemoReportData | undefined, texts: string[]) {
  const table = (headers: string[], rows: unknown[][]) =>
    [headers, headers.map(() => '---'), ...rows]
      .map(row => `| ${row.map(v => String(v ?? '—').replaceAll('|', '\\|')).join(' | ')} |`)
      .join('\n');
  const payer = r?.top_payer;
  const metadata = r && [
    `Borrower: ${r.borrower}`,
    `Bank and account: ${r.bank} ${r.account}`,
    `Statement period: ${r.period.start} – ${r.period.end}`,
    `Activity: ${activityText(r)}`,
    `Currency: ${r.currency}`,
    `Prepared for: ${r.audience}`,
    `Date: ${r.prepared}`,
    `Evidence: ${r.evidence.statements} statement pages, ${r.evidence.invoices} invoice pages`,
  ].join('\n');
  return [
    '# Credit memo · Cash-flow review',
    metadata,
    texts[0],
    r && table(['Month', 'Money in', 'Money out', 'Net'], [
      ...r.monthly.map(m => [
        m.month + (m.partial ? ' (partial)' : ''), m.inflow, m.outflow, m.net,
      ]),
      ['Total', r.totals.inflow, r.totals.outflow, r.totals.net],
    ]),
    texts[1],
    payer && [
      `${payer.payer}: ${r?.currency} ${payer.amount} of ${r?.currency} ${payer.total_inflow}`,
      `(${payer.share_percent}%) ${payer.cites.join(' ')}`,
    ].join(' '),
    texts[2],
    r && table(['Flag', 'Amount', 'Evidence', 'Detail'],
      r.findings.map(f => [f.label, f.amount, f.cites.join(' '), f.description])),
    r && table(['Page', 'Printed', 'Expected', 'Difference'],
      r.reconciliation.filter(p => !p.ok)
        .map(p => [p.page_id, p.printed, p.expected, p.difference])),
    texts[3],
    texts[4],
    reportFooter,
    'Figures come from code and are checked; click a [p:r] reference to see the row.',
  ].filter(Boolean).join('\n\n');
}

export function monthLabel(month: string) {
  const date = new Date(month + '-01T00:00:00');
  return Number.isNaN(date.getTime()) ? month : date.toLocaleDateString('en-GB', {month: 'short', year: 'numeric'});
}

export function activityText(r: MemoReportData) {
  const months = r.monthly.map(m => m.month).sort();
  if (!months.length) return 'No activity';
  const month = (value: string) => new Date(value + '-01T00:00:00').toLocaleDateString('en-GB', {
    month: 'short'
  });
  const first = months[0],
    last = months[months.length - 1];
  const range = first.slice(0, 4) === last.slice(0, 4) ? `${month(first)} – ${month(last)} ${last.slice(0, 4)}` : `${month(first)} ${first.slice(0, 4)} – ${month(last)} ${last.slice(0, 4)}`;
  return `${range} (${r.totals.months} months, ${r.totals.full_months} full)`;
}
export function ReportDialog({
  children,
  onClose
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.current?.querySelector<HTMLElement>('button')?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex="0"]') || []);
      const first = focusable[0],
        last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', keydown, true);
    return () => {
      document.removeEventListener('keydown', keydown, true);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [onClose]);
  return <div className="report-overlay" ref={dialog} role="dialog" aria-modal="true" aria-label="Full credit report">
    <div className="report-reading">
      <div className="report-close-bar"><button className="secondary" onClick={onClose}>Close</button></div>
      {children}
    </div>
  </div>;
}
export function MemoReport({
  report: r,
  finished,
  problems,
  children,
  texts
}: {
  report?: MemoReportData;
  finished: boolean;
  problems: number;
  children: ReactNode;
  texts: string[];
}) {
  const [open, setOpen] = useState(false);
  const closeReport = useCallback(() => setOpen(false), []);
  const money = (v: unknown) => v == null ? '—' : `${r?.currency || ''} ${Number(v).toLocaleString('en', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`;
  const date = (v: string) => new Date(v + 'T00:00:00').toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  });
  const paper = <div className="report-paper">
    <header className="report-header">
      <h2>Credit memo · Cash-flow review</h2>
      <span className={`report-status ${problems ? 'report-orange' : 'report-good'}`}>
        {!finished ? 'Writing…' : problems ? `${problems} checks flagged` : 'All figures checked'}
      </span>
      <button className="secondary" onClick={() => setOpen(true)}>Open full report</button>
    </header>
    {r && <>
      <dl className="report-grid">
        {Object.entries({
          Borrower: r.borrower,
          'Bank and account': `${r.bank} · ${r.account}`,
          'Statement period': `${date(r.period.start)} – ${date(r.period.end)}`,
          Activity: activityText(r),
          Currency: r.currency,
          Evidence: `${r.evidence.statements} statement pages, ${r.evidence.invoices} invoice${r.evidence.invoices === 1 ? '' : 's'}`,
          'Prepared for': r.audience,
          Date: date(r.prepared)
        }).map(([k, v]) => <div key={k}>
          <dt>
            {k}
          </dt>
          <dd>
            {v}
          </dd>
        </div>)}
      </dl>
      <h3>Key figures</h3>
      <div className="report-figures">
        {[
          ['Money in', money(r.totals.inflow)],
          ['Money out', money(r.totals.outflow)],
          ['Net', money(r.totals.net)],
          ['Average balance', money(r.balances.average.value)],
          ['Lowest balance', money(r.balances.minimum.value)],
          ['Largest payer share', r.top_payer ? `${r.top_payer.share_percent}%` : '—'],
          ['Red flags', String(r.findings.length)],
        ].map(([k, v]) => <div key={k}>
          <small>
            {k}
          </small>
          <strong className={k === 'Net' ? Number(r.totals.net) < 0 ? 'report-negative' : 'report-good' : ''}>
            {typeof v === 'string' && v.startsWith(r.currency + ' ') ? <>
              <small className="report-currency">
                {r.currency}
              </small>
              <span className="report-nowrap">
                {v.slice(r.currency.length + 1)}
              </span>
            </> : v}
          </strong>
        </div>)}
      </div>
    </>}
    {children}
    <footer className="report-footer">
      <p>
        {reportFooter}
      </p>
      <p>Figures come from code and are checked; click a [p:r] reference to see the row.</p>
      <button className="secondary" onClick={() => void navigator.clipboard?.writeText(reportMarkdown(r, texts)).catch(() => { })}>Copy memo</button>
    </footer>
  </div>;
  return <>
    {paper}
    {open && <ReportDialog onClose={closeReport}>
      {paper}
    </ReportDialog>}
  </>;
}
function columnClass(header: string) {
  const amounts = ['Money in', 'Money out', 'Net', 'Amount', 'Printed', 'Expected', 'Difference'];
  if (amounts.includes(header)) return 'report-amount report-nowrap';
  return header === 'Flag' ? 'report-nowrap' : undefined;
}

export function ReportTable({
  headers,
  rows
}: {
  headers: string[];
  rows: ReactNode[][];
}) {
  return <div className="report-table-scroll">
    <table className="report-table">
      <thead>
        <tr>
          {headers.map(h => <th key={h} className={columnClass(h)}>
            {h}
          </th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => <tr key={i}>
          {row.map((cell, j) => <td key={j} className={columnClass(headers[j])}>
            {cell}
          </td>)}
        </tr>)}
      </tbody>
    </table>
  </div>;
}
export function ReportSectionData({
  id,
  report: r,
  jump
}: {
  id: string;
  report?: MemoReportData;
  jump: (c: Citation) => void;
}) {
  if (!r) return null;
  const money = (v: unknown) => v == null ? '—' : `${r.currency} ${Number(v).toLocaleString('en', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`;
  const cites = (v: string[]) => <MemoContent text={v.join(' ')} jump={jump} />;
  if (id === 'cash_flow') return <ReportTable
    headers={['Month', 'Money in', 'Money out', 'Net']} rows={[...r.monthly.map(m => [<>
      {monthLabel(m.month)}
      {m.partial && <small className="report-partial"> partial</small>}
    </>, money(m.inflow), money(m.outflow), money(m.net)]), ['Total', money(r.totals.inflow), money(r.totals.outflow), money(r.totals.net)]]} />;
  if (id === 'concentration' && r.top_payer) {
    const p = r.top_payer;
    return <div>
      <strong>
        {p.payer}
      </strong>
      <p>
        {money(p.amount)}
        {' of '}
        {money(p.total_inflow)}
      </p>
      <div className="report-share" role="meter" aria-label="Largest payer share" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Number(p.share_percent)}>
        <span style={{
          width: `${Math.max(0, Math.min(100, Number(p.share_percent)))}%`
        }} />
      </div>
      <div>
        {p.share_percent}
        %
        {cites(p.cites)}
      </div>
    </div>;
  }
  if (id === 'red_flags') return <>
    <ReportTable
      headers={['Flag', 'Amount', 'Evidence', 'Detail']}
      rows={r.findings.map(f => [f.label, money(f.amount), cites(f.cites), f.description])} />
    {r.reconciliation.some(p => !p.ok) && <>
      <h4>Reconciliation</h4>
      <ReportTable
        headers={['Page', 'Printed', 'Expected', 'Difference']}
        rows={r.reconciliation.filter(p => !p.ok).map(p => [p.page_id, money(p.printed), money(p.expected), money(p.difference)])} />
    </>}
  </>;
  return null;
}
