export function csvText(rows: unknown[][]) {
  return rows.map(row => row.map(value => {
    // Prevent spreadsheet formulas when exporting editable or model-provided text.
    const text = String(value ?? '');
    const safe = /^[=+@\t\r]/.test(text) || /^-\D/.test(text) ? `'${text}` : text;
    return `"${safe.replaceAll('"', '""')}"`;
  }).join(',')).join('\r\n');
}
export function downloadCSV(name: string, rows: unknown[][]) {
  const url = URL.createObjectURL(new Blob([csvText(rows)], {type: 'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
