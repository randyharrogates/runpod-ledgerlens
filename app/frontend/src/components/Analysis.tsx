import {
  Bar, Cell, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Analysis, Citation } from '../api';
import { money, monthName } from './format';

export function monthlyPoints(monthly: Analysis['monthly']) {
  return Object.entries(monthly).map(([month, value]) => ({
    month,
    partial: value.partial,
    daysObserved: value.days_observed,
    inflow: Number(value.inflow),
    outflow: Number(value.outflow),
    net: Number(value.net),
  }));
}

export function Cashflow({ data, jump }: {
  data: Analysis | null;
  jump: (citation: Citation) => void;
}) {
  if (!data || !Object.keys(data.monthly).length) return <p>Extract statements to compute cash flow.</p>;
  const currency = data.currency || 'SGD';
  const points = monthlyPoints(data.monthly);
  const kpis = [
    ['Average observed balance', money(data.average_balance.value, currency, true)],
    ['Minimum observed balance', money(data.minimum_balance.value, currency, true)],
    ["Biggest customer's share of money in", `${(Number(data.top_payer_concentration.share) * 100).toFixed(1)}%`],
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
            <XAxis dataKey="month" tickFormatter={month => `${monthName(month)}${data.monthly[month].partial ? " · partial" : ""}`} />
            <YAxis width={65} tickFormatter={value => Intl.NumberFormat('en', { notation: 'compact' }).format(Number(value))} label={{ value: currency, angle: -90, position: 'insideLeft' }} />
            <Tooltip formatter={value => money(value, currency, true)} />
            <Legend />
            <Bar dataKey="inflow" fill="#5CC8FF">
              {points.map(point => <Cell key={point.month} fillOpacity={point.partial ? 0.45 : 1} />)}
            </Bar>
            <Bar dataKey="outflow" fill="#B9A6FF">
              {points.map(point => <Cell key={point.month} fillOpacity={point.partial ? 0.45 : 1} />)}
            </Bar>
            <Line dataKey="net" stroke="#7EE2A8" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {points.some(point => point.partial) && <p>{points.filter(point => point.partial).map(point =>
        `${new Date(`${point.month}-01T00:00:00`).toLocaleDateString('en', { month: 'long' })} is partial (${point.daysObserved} days observed)`
      ).join('; ')}.</p>}
      <h2>Flags to investigate</h2>
      {data.findings.length === 0 && <p>No red flags found</p>}
      {data.findings.map((finding, i) => (
        <article className="flag" key={i}>
          <span>{finding.description || finding.type.replaceAll('_', ' ')}</span>
          <span className="citations">
            {finding.citations.map((citation, j) => (
              <button key={j} onClick={() => jump(citation)}>
                {citation.page_id} · line {citation.row_index + 1}
              </button>
            ))}
          </span>
        </article>
      ))}
    </>
  );
}
