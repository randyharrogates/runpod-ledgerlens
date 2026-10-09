import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Analysis, Citation } from '../api';
import { money } from './format';

export function Cashflow({ data, jump }: {
  data: Analysis | null;
  jump: (citation: Citation) => void;
}) {
  if (!data) return <p>Extract statements to compute cash flow.</p>;
  const currency = data.currency || 'SGD';
  const points = Object.entries(data.monthly).map(([month, value]) => ({
    month,
    inflow: Number(value.inflow),
    outflow: Number(value.outflow),
    net: Number(value.net),
  }));
  const kpis = [
    ['Average observed balance', money(data.average_balance.value, currency, true)],
    ['Minimum observed balance', money(data.minimum_balance.value, currency, true)],
    ['Top payer share', `${(Number(data.top_payer_concentration.share) * 100).toFixed(1)}%`],
  ];
  return (
    <>
      <div className="kpis">
        {kpis.map(([label, value]) => (
          <article key={label}><small>{label}</small><h2>{value}</h2></article>
        ))}
      </div>
      <div className="chart">
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart data={points} margin={{ left: 20, right: 20 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="month" />
            <YAxis width={125} tickFormatter={value => money(value, currency, true)} />
            <Tooltip formatter={value => money(value, currency, true)} />
            <Legend />
            <Bar dataKey="inflow" fill="#12685f" />
            <Bar dataKey="outflow" fill="#b8c8c5" />
            <Line dataKey="net" stroke="#bb713b" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <h2>Flags to investigate</h2>
      {data.findings.length === 0 && <p>No red flags found</p>}
      {data.findings.map((finding, i) => (
        <article className="flag" key={i}>
          <span>{finding.description || finding.type.replaceAll('_', ' ')}</span>
          <span className="citations">
            {finding.citations.map((citation, j) => (
              <button key={j} onClick={() => jump(citation)}>
                {citation.page_id}:r{citation.row_index + 1}
              </button>
            ))}
          </span>
        </article>
      ))}
    </>
  );
}
