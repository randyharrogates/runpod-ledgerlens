import React from 'react';

export function ResultTable({ value }: { value: unknown }): React.ReactElement {
  if (Array.isArray(value)) {
    const rows: Record<string, unknown>[] = value.map(item => (
      typeof item === 'object' && item !== null
        ? item as Record<string, unknown>
        : { value: item }
    ));
    const fields = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
    return (
      <div className="table-scroll">
        <table>
          <thead>
            <tr>{fields.map(field => <th key={field}>{field.replaceAll('_', ' ')}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {fields.map(field => <td key={field}><ResultTable value={row[field]} /></td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (typeof value === 'object' && value !== null) {
    return (
      <table>
        <tbody>
          {Object.entries(value).map(([key, item]) => (
            <tr key={key}>
              <th>{key.replaceAll('_', ' ')}</th>
              <td><ResultTable value={item} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return <>{String(value ?? '—')}</>;
}
