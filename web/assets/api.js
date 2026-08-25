/* api.js — the only place that knows the endpoint shapes.
 *
 * Every call goes through here so a route change is one edit. Errors surface:
 * a failed fetch throws with the status and the server's message rather than
 * resolving to an empty list, because a silently empty table is the failure
 * mode this rebuild exists to eliminate.
 */

const BASE = '/api';

async function get(path) {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep statusText */ }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}

async function post(path) {
  const res = await fetch(`${BASE}${path}`, { method: 'POST' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  return body;
}

export const api = {
  meetings: (limit = 50) => get(`/meetings?limit=${limit}`),
  meeting: (date) => get(`/meeting/${date}`),
  race: (date, no) => get(`/race/${date}/${no}`),
  horse: (name, limit = 6) => get(`/horse/${encodeURIComponent(name)}?limit=${limit}`),
  raceCard: (date, no) => get(`/raceday/${date}/${no}`),
  raceDayMeeting: (date) => get(`/raceday/${date}`),
  concentration: (date, no) => get(`/market/concentration/${date}/${no}`),
  coverage: () => get('/market/coverage'),
  formGuide: (date, no, history = 6) =>
    get(`/formguide/${date}/${no}?history=${history}`),
  raceQuality: (date, no) => get(`/race-quality/${date}/${no}`),
  etRace: (date, no) => get(`/model/et/${date}/${no}`),
  etSummary: () => get('/model/et/summary'),
  status: () => get('/status'),
  rebuildEt: (months = 24) => post(`/jobs/rebuild-et?window_months=${months}`),
};

/** Numbers never render bare: a null becomes an explicit dash, not a blank. */
export function num(v, digits = 1) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(digits);
}

export function signed(v, digits = 1) {
  if (v === null || v === undefined) return '—';
  return `${v >= 0 ? '+' : ''}${Number(v).toFixed(digits)}`;
}
