export function money(value: unknown, currency = 'SGD', code = false): string {
  if (value == null || value === '') return '—';
  const digits = currency === 'IDR' ? 0 : 2;
  const number = Number(value).toLocaleString('en-SG', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
  return code ? `${currency} ${number}` : number;
}

export function printedDate(value: string | null, pattern?: string | null): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return value || '';
  void pattern;
  const [year, , day] = value.split('-');
  return `${Number(day)} ${new Date(`${value}T12:00:00Z`).toLocaleString('en-GB', {month: 'short', timeZone: 'UTC'})} ${year}`;
}

export function number(value: unknown): string {
  return value == null ? '—' : Number(value).toLocaleString('en-US', {maximumFractionDigits: 2});
}
export const dollars = (value: number) => `$${value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
export function layoutName(value: string): string {
  return (value || 'Layout not reported').replace(/^stmt_([a-z])$/i, (_, letter: string) => `Statement ${letter.toUpperCase()}`).replace(/^invoice_([a-z])$/i, (_, letter: string) => `Invoice ${letter.toUpperCase()}`);
}
export const groupName = (value: string) => ({train:'Learn from',val:'Check progress',test:'Final test',test_unseen_layout:'final test, unseen layout',test_seen_layout:'final test, familiar layout',test_cord:'final test, real receipts'}[value] || value);
export const currencyName = (value: string) => ({SGD:'Singapore dollars',MYR:'Malaysian ringgit',IDR:'Indonesian rupiah'}[value] || value);
export function monthName(value: string): string {
  return new Date(`${value}-01T12:00:00Z`).toLocaleDateString('en-GB', {month:'short',year:'numeric',timeZone:'UTC'});
}
export const metadata = (meta: Record<string,unknown>, currency?: string) => `${layoutName(String(meta.layout_id || 'Layout not reported'))}${['stmt_e','stmt_f'].includes(String(meta.layout_id)) ? ' (held back)' : ''} · ${String(meta.strength || 'unknown')} photo damage${currency ? ` · ${currencyName(currency)}` : ''}`;
