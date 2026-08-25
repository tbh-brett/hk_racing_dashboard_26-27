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

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    ...(body === undefined ? {} : {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail ?? `${res.status} ${res.statusText}`);
  return out;
}

export const api = {
  meetings: (limit = 50) => get(`/meetings?limit=${limit}`),
  meeting: (date) => get(`/meeting/${date}`),
  horses: (limit = 400, q) => get(
    `/horses?limit=${limit}` + (q ? `&q=${encodeURIComponent(q)}` : '')),
  race: (date, no) => get(`/race/${date}/${no}`),
  horse: (name, limit = 6) => get(`/horse/${encodeURIComponent(name)}?limit=${limit}`),
  raceCard: (date, no) => get(`/raceday/${date}/${no}`),
  raceDayMeeting: (date) => get(`/raceday/${date}`),
  meetingBlackbook: (date) => get(`/raceday/${date}/blackbook`),
  blackbook: (q = '') => get(`/blackbook${q}`),
  blackbookEntry: (id) => get(`/blackbook/${encodeURIComponent(id)}`),
  blackbookTags: () => get('/blackbook/tags'),
  blackbookDeclared: (date) => get(`/blackbook/declared/${date}`),
  backedVsMissed: (entryId) => get(
    `/blackbook/backed-vs-missed${entryId ? `?entry_id=${entryId}` : ''}`),
  bets: (q = '') => get(`/bets${q}`),
  betsSummary: () => get('/bets/summary'),
  betsForRace: (date, no) => get(`/bets/race/${date}/${no}`),
  betsAnalysis: (account) => get(
    `/bets/analysis${account ? `?account=${encodeURIComponent(account)}` : ''}`),
  betsReconciliation: (account) => get(
    `/bets/reconciliation${account ? `?account=${encodeURIComponent(account)}` : ''}`),
  lookup: (q) => get(`/lookup?${q}`),
  lookupInsight: (q) => get(`/lookup/insight?${q}`),
  lookupFilters: () => get('/lookup/filters'),
  blackbookSummary: (today) => get(
    `/blackbook/summary${today ? `?today=${today}` : ''}`),
  setBlackbookStatus: (id, status) => post(
    `/blackbook/${encodeURIComponent(id)}/status`, { status }),
  concentration: (date, no) => get(`/market/concentration/${date}/${no}`),
  coverage: () => get('/market/coverage'),
  formGuide: (date, no, history = 6) =>
    get(`/formguide/${date}/${no}?history=${history}`),
  raceQuality: (date, no) => get(`/race-quality/${date}/${no}`),
  racePace: (date, no) => get(`/pace/${date}/${no}`),
  conditionFit: (name, q = '') =>
    get(`/condition-fit/${encodeURIComponent(name)}${q}`),
  headToHead: (a, b, before) => get(
    `/head-to-head/${encodeURIComponent(a)}/${encodeURIComponent(b)}`
    + (before ? `?before=${before}` : '')),
  notes: (horses) => get(`/notes?horses=${encodeURIComponent(horses.join(','))}`),
  saveNote: (body) => post('/notes', body),
  createBlackbookEntry: (body) => post('/blackbook', body),
  etRace: (date, no) => get(`/model/et/${date}/${no}`),
  etSummary: () => get('/model/et/summary'),
  sarrRace: (date, no) => get(`/model/sarr/${date}/${no}`),
  blendRace: (date, no, weight) => get(
    `/model/blend/${date}/${no}` + (weight === undefined ? '' : `?weight=${weight}`)),
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
