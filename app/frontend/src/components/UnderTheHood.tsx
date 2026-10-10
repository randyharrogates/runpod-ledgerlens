import { models, modelState } from '../models';
import { Term } from './Term';
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
      <td>{models.find(m => m.key === request.model)?.name || 'Unknown'}</td>
      {cached ? <td colSpan={6}>cached — no GPU used
        {Boolean(request.live_error) && <details className="live-warning">
          <summary className="chip warning" title={String(request.live_error)}>Live call failed — showing cached result</summary>
          <small>{String(request.live_error)}</small>
        </details>}
      </td> : (
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
  return <div className="endpoint"><strong>{models.find(m => m.key === model)?.name}</strong><span className="chip">{modelState(status, model)}</span></div>;
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
      <summary>Timing and cost</summary>
      <p>
        {history.length} requests · USD {cost(total)} total cost ·{' '}
        {average == null ? 'No measured inference' : `${seconds(average)} s/page average inference`}
        {' · '}{cold} cold starts
      </p>
      <p className="hood-note">
        delayTime is waiting for a GPU; executionTime is time spent actually reading. Cost uses executionTime.
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
      <p><Term name="cold start" /></p><h3>Model status</h3>
      {['ft', '8b', '4b'].map(model => <EndpointStatus key={model} model={model} status={status} />)}
    </details>
  );
}
