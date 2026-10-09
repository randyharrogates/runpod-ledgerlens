export function money(value: unknown, currency = 'SGD', code = false): string {
  if (value == null || value === '') return '—';
  const digits = currency === 'IDR' ? 0 : 2;
  const number = Number(value).toLocaleString('en-SG', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return code ? `${currency} ${number}` : number;
}

export function printedDate(value: string | null, pattern?: string | null): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return value || '';
  const [year, month, day] = value.split('-');
  if (pattern === '%d/%m/%Y') return `${day}/${month}/${year}`;
  if (pattern === '%d %b %Y') {
    const label = new Date(`${value}T12:00:00Z`).toLocaleString('en-SG', {
      month: 'short',
      timeZone: 'UTC',
    });
    return `${Number(day)} ${label} ${year}`;
  }
  return value;
}
