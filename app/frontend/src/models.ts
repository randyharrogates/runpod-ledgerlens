export const models = [
  { key: 'ft', name: 'Trained small model', technical: 'Qwen3-VL · 4B fine-tuned' },
  { key: '8b', name: 'Big model', technical: 'Qwen3-VL · 8B' },
  { key: '4b', name: 'Small model', technical: 'Qwen3-VL · 4B base' },
];
export function modelState(status: Record<string, unknown>, key: string): string {
  const endpoint = status[key] as Record<string, unknown> | undefined;
  if (!endpoint) return 'unavailable';
  if (endpoint.offline || endpoint.configured === false) return 'offline';
  if (endpoint.unavailable || endpoint.error) return 'unavailable';
  const workers = endpoint.workers as Record<string, number> | undefined;
  const jobs = endpoint.jobs as Record<string, number> | undefined;
  if ((workers?.idle || 0) + (workers?.running || 0) > 0) return 'ready';
  if ((workers?.initializing || 0) > 0 || (jobs?.inQueue || 0) > 0) return 'waking up';
  if (workers && ['idle', 'running', 'initializing'].every(key => workers[key] === 0) && Object.values(workers).every(value => value === 0) && jobs && (jobs.inQueue || 0) === 0) return 'asleep';
  return 'unavailable';
}

export function statusSummary(status: Record<string, unknown>): string | null {
  const states = models.map(m => modelState(status, m.key));
  if (states.every(s => s === 'offline' || s === 'unavailable')) return 'Live models off · recorded results still work';
  if (states.every(s => s === 'asleep')) return 'Live models asleep · first read wakes them (can take minutes)';
  if (states.every(s => s === 'ready')) return 'All 3 models ready';
  if (states.every(s => s === 'waking up')) return 'Live models waking up';
  return null;
}
