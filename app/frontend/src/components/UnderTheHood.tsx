import { money } from './format';

type Request = Record<string, unknown>;
const seconds = (value: unknown) => value == null ? '—' : Number(value).toFixed(2);
const cost = (value: unknown) => value == null ? '—' : Number(value).toFixed(4);
const columns = [
  'Page', 'Task', 'Model', 'GPU', 'Cold/warm', 'Queue + cold start (s)',
  'Inference (s)', 'Cost (USD)', 'USD / 1,000 pages',
];

function inference(request: Request): number | null {
  const timing = request.timing as Record<string, unknown> | undefined;
  if (request.source === 'cached-demo') return null;
  if (timing?.inference_s != null) return Number(timing.inference_s);
  return request.executionTime != null ? Number(request.executionTime) / 1000 : null;
}

function RequestRow({ request }: { request: Request }) {
  const cached = request.source === 'cached-demo';
  const startup = request.delayTime == null ? null : Number(request.delayTime) / 1000;
  const temperature = request.cold_start === true
    ? 'cold' : request.cold_start === false ? 'warm' : 'Not reported';
  return (
    <tr>
      <td>{String(request.page_id || 'Session')}</td>
      <td>{String(request.task)}</td>
      <td>{String(request.model || 'Unknown')}</td>
      {cached ? <td colSpan={6}>cached — no GPU used</td> : (
        <>
          <td>{String(request.gpu || 'Not reported')}</td>
          <td>{temperature}</td>
          <td className="numeric">{seconds(startup)}</td>
          <td className="numeric">{seconds(inference(request))}</td>
          <td className="numeric">{cost(request.cost)}</td>
          <td className="numeric">
            {request.task === 'extract' ? cost(request.cost_per_1000_pages) : 'Not applicable'}
          </td>
        </>
      )}
    </tr>
  );
}

function EndpointStatus({ model, status }: {
  model: string;
  status: Record<string, unknown>;
}) {
  const endpoint = status[model] as Record<string, unknown> | undefined;
  const workers = endpoint?.workers as Record<string, number> | undefined;
  const jobs = endpoint?.jobs as Record<string, number> | undefined;
  let message = '';
  if (!endpoint) message = status.connection ? 'Unavailable' : 'not configured';
  else if (endpoint.offline) message = 'Offline demo';
  else if (endpoint.unavailable) message = 'Unavailable';
  return (
    <div className="endpoint">
      <strong>{model}</strong>
      {message ? <span className="chip">{message}</span> : (
        <>
          <span className="chip">{workers?.idle || 0} idle workers</span>
          <span className="chip">{workers?.running || 0} running workers</span>
          <span className="chip">{workers?.initializing || 0} initializing workers</span>
          <span className="chip">{jobs?.inQueue || 0} jobs in queue</span>
        </>
      )}
    </div>
  );
}

export function UnderTheHood({ history, status }: {
  history: Request[];
  status: Record<string, unknown>;
}) {
  const total = history.reduce((sum, request) => sum + Number(request.cost || 0), 0);
  const pages = history.filter(request => request.task === 'extract' && inference(request) != null);
  const average = pages.length
    ? pages.reduce((sum, request) => sum + (inference(request) || 0), 0) / pages.length
    : null;
  const cold = history.filter(request => request.cold_start === true).length;
  const usage = history.filter(request => request.task === 'memo').map(request => (
    Object.entries((request.usage || {}) as Record<string, unknown>)
      .map(([key, value]) => `${key.replaceAll('_', ' ')}: ${money(value, 'IDR')}`)
      .join(' · ')
  )).filter(Boolean).join('; ');
  return (
    <details className="hood">
      <summary>Under the hood · timing, GPU and cost</summary>
      <p>
        {history.length} requests · USD {cost(total)} total cost ·{' '}
        {average == null ? 'No measured inference' : `${seconds(average)} s/page average inference`}
        {' · '}{cold} cold starts
      </p>
      <p className="hood-note">
        Queue + cold start uses Runpod delayTime; cost uses executionTime.
        Cached requests incur no GPU cost. Inference averages include measured extraction pages only.
      </p>
      <div className="table-scroll">
        <table className="request-table">
          <thead><tr>{columns.map(label => <th key={label}>{label}</th>)}</tr></thead>
          <tbody>
            {history.map((request, i) => <RequestRow key={i} request={request} />)}
          </tbody>
        </table>
      </div>
      {!history.length && <p>No requests yet.</p>}
      {usage && <p className="hood-note">Memo usage: {usage}</p>}
      <h3>Live endpoint status</h3>
      {['8b', 'ft'].map(model => <EndpointStatus key={model} model={model} status={status} />)}
    </details>
  );
}
