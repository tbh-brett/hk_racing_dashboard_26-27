/* Blackbook — ported from web/design-source/Blackbook.dc.html.
 *
 * A hypothesis tracker, not a list of favourites. Every entry is a claim that
 * this horse will run better than its public form suggests, for a stated
 * reason, and the page's job is to show whether those claims pay off.
 *
 * The two columns that carry that are RUNS SINCE and RECORD SINCE, and both
 * are DERIVED from the runners table rather than read from what anyone
 * remembered to log. The legacy export recorded a subsequent run for 25 of its
 * 196 entries; the derivation finds 355 across the same 196. That difference
 * is the page.
 */
import { api, num, signed } from './api.js';
import { el, $, DASH, MINUS, renderNav, styleClass } from './vocab.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';


const COLS = [
  { key: 'caret', label: '', cls: 'c' },
  { key: 'name', label: 'HORSE' },
  { key: 'tags', label: 'BOOKED FOR' },
  { key: 'added', label: 'BOOKED' },
  { key: 'source', label: 'SOURCE RUN' },
  { key: 'runs', label: 'RUNS', cls: 'r' },
  { key: 'record', label: 'RECORD', cls: 'r' },
  { key: 'next', label: 'NEXT' },
  { key: 'status', label: 'STATUS' },
  { key: 'acts', label: '' },
];

const STATUS_TABS = [
  ['all', 'ALL'], ['active', 'ACTIVE'], ['expired', 'EXPIRED'],
  ['won_out', 'WON OUT'], ['retired', 'RETIRED'],
];

// Booked within the last N days. The book turns over in about a season, so the
// useful cuts are inside a month, inside a quarter, and everything else.
const RANGES = [
  ['all', 'ALL', null], ['30', '30d', 30], ['90', '90d', 90], ['old', '90d+', -90],
];

// Under this many runs a tag has not been tested, whatever its strike rate.
const THIN_RUNS = 20;

const state = {
  view: 'list', entries: [], tags: [], summary: null, tagMeta: null,
  search: '', tag: null, status: 'all', range: 'all', today: null,
  todayOnly: false, declared: new Set(), open: new Set(), details: {},
  backedMissed: null, account: null, tagBvm: null,
  sort: 'added', sortDir: -1, busy: new Set(),
};

/* ── chrome ──────────────────────────────────────────────────────────────── */

function renderViewToggle() {
  $('view-toggle').replaceChildren(...[['list', 'LIST'], ['analysis', 'ANALYSIS']]
    .map(([key, label]) => {
      const b = el('button', null, label);
      b.setAttribute('aria-pressed', String(state.view === key));
      b.addEventListener('click', () => { state.view = key; render(); });
      return b;
    }));
}

function pct(v, digits = 1) {
  return v === null || v === undefined ? DASH : `${(v * 100).toFixed(digits)}%`;
}

function signedPct(v) {
  if (v === null || v === undefined) return DASH;
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
}

function renderSummary() {
  const s = state.summary;
  const host = $('bb-sum');
  if (!s) { host.replaceChildren(); return; }
  host.replaceChildren();
  const stat = (value, label, cls) => {
    const box = el('span');
    box.append(el('b', cls ?? null, String(value)));
    box.append(document.createTextNode(` ${label}`));
    return box;
  };
  host.append(stat(s.total, 'ENTRIES'));
  host.append(stat(s.active, 'ACTIVE', 'teal'));
  host.append(stat(s.declared_today, `RUN ${s.today ?? 'TODAY'}`, 'amber'));
  host.append(el('span', 'pipe', '|'));
  host.append(stat(s.runs_since, 'RUNS SINCE BOOKING'));

  const roi = el('span');
  roi.append(document.createTextNode('FLAT ROI '));
  roi.append(el('b', s.flat_roi >= 0 ? 'pos' : 'neg', signedPct(s.flat_roi)));
  host.append(roi);

  const ae = el('span');
  ae.append(document.createTextNode('A/E '));
  ae.append(el('b', null, s.ae === null ? DASH : s.ae.toFixed(2)));
  ae.append(document.createTextNode(
    s.ae === null ? '' : ` (${s.ae_lo}–${s.ae_hi})`));
  ae.title = `${s.wins_since} wins against ${s.expected_wins} the market implied `
    + `over ${s.ae_runs} fully priced runs`;
  host.append(ae);

  // The interval straddling 1.00 is the finding, not a gap in the data.
  const clears = s.ae !== null && (s.ae_lo > 1 || s.ae_hi < 1);
  host.append(el('span', 'caveat', clears
    ? 'THE BOOK BEATS THE PRICE AT 95%'
    : 'INTERVAL STRADDLES 1.00 — NOT SHOWN TO BEAT THE PRICE'));
}

/* ── filters ─────────────────────────────────────────────────────────────── */

function tagCounts() {
  const counts = {};
  state.entries.forEach((e) => e.tags.forEach((t) => {
    counts[t] = (counts[t] ?? 0) + 1;
  }));
  return counts;
}

function renderChips() {
  const counts = tagCounts();
  $('tag-chips').replaceChildren(...Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([tag, n]) => {
      const b = el('button', `chip${state.tag === tag ? ' on' : ''}`);
      b.append(document.createTextNode(tag));
      b.append(el('span', 'n', String(n)));
      b.title = state.tagMeta?.[tag] ?? '';
      b.addEventListener('click', () => {
        state.tag = state.tag === tag ? null : tag;
        render();
      });
      return b;
    }));

  $('range-chips').replaceChildren(...RANGES.map(([key, label]) => {
    const b = el('button', `chip${state.range === key ? ' on' : ''}`, label);
    b.addEventListener('click', () => { state.range = key; render(); });
    return b;
  }));

  const today = el('button', `chip${state.todayOnly ? ' on' : ''}`);
  today.append(document.createTextNode('RUNNING TODAY '));
  today.append(el('span', 'n', String(state.declared.size)));
  today.title = state.summary?.today
    ? `declared at the ${state.summary.today} meeting`
    : 'no meeting loaded';
  today.addEventListener('click', () => { state.todayOnly = !state.todayOnly; render(); });
  $('today-chip').replaceChildren(today);

  const byStatus = {};
  state.entries.forEach((e) => { byStatus[e.status] = (byStatus[e.status] ?? 0) + 1; });
  $('status-tabs').replaceChildren(...STATUS_TABS.map(([key, label]) => {
    const b = el('button');
    b.setAttribute('aria-pressed', String(state.status === key));
    b.append(document.createTextNode(`${label} `));
    b.append(el('span', 'n', String(
      key === 'all' ? state.entries.length : byStatus[key] ?? 0)));
    b.addEventListener('click', () => { state.status = key; render(); });
    return b;
  }));
}

function activeFilters() {
  const out = [];
  if (state.search) out.push(['SEARCH', state.search, () => { state.search = ''; }]);
  if (state.tag) out.push(['TAG', state.tag, () => { state.tag = null; }]);
  if (state.status !== 'all') {
    out.push(['STATUS', state.status, () => { state.status = 'all'; }]);
  }
  if (state.range !== 'all') {
    const r = RANGES.find((x) => x[0] === state.range);
    out.push(['BOOKED', r[1], () => { state.range = 'all'; }]);
  }
  if (state.todayOnly) out.push(['', 'RUNNING TODAY', () => { state.todayOnly = false; }]);
  return out;
}

function renderActiveFilters(matched) {
  const host = $('active-filters');
  const active = activeFilters();
  host.replaceChildren();
  if (!active.length) {
    host.append(el('span', 'none',
      `NO FILTERS — SHOWING ALL ${state.entries.length}`));
  } else {
    host.append(el('span', 'lab', 'ACTIVE'));
    active.forEach(([kind, label, clear]) => {
      const b = el('button', 'chip on');
      if (kind) b.append(el('span', 'kind', kind));
      b.append(document.createTextNode(label));
      b.append(document.createTextNode(' ×'));
      b.addEventListener('click', () => { clear(); render(); });
      host.append(b);
    });
    const all = el('button', 'chip clear-all', 'CLEAR ALL ×');
    all.addEventListener('click', () => {
      state.search = '';
      state.tag = null;
      state.status = 'all';
      state.range = 'all';
      state.todayOnly = false;
      $('search').value = '';
      render();
    });
    host.append(all);
  }
  $('clear-search').hidden = !state.search;

  const count = $('match-count');
  count.replaceChildren();
  count.append(el('b', null, String(matched)));
  count.append(document.createTextNode(`OF ${state.entries.length} MATCH`));
}

function daysAgo(date) {
  const t = Date.parse(date);
  return Number.isNaN(t) ? null : Math.round((Date.now() - t) / 86400000);
}

function matches(e) {
  if (state.tag && !e.tags.includes(state.tag)) return false;
  if (state.status !== 'all' && e.status !== state.status) return false;
  if (state.todayOnly && !state.declared.has(e.horse_name)) return false;
  if (state.range !== 'all') {
    const days = daysAgo(e.added_date);
    const limit = RANGES.find((r) => r[0] === state.range)[2];
    if (days === null) return false;
    if (limit > 0 && days > limit) return false;
    if (limit < 0 && days <= -limit) return false;
  }
  if (state.search) {
    const q = state.search.toLowerCase();
    const hay = [e.horse_name, e.reasoning ?? '', e.tags.join(' '),
                 e.source_race ?? ''].join(' ').toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function sortValue(e, key) {
  switch (key) {
    case 'name': return e.horse_name;
    case 'tags': return e.tags[0] ?? 'zzz';
    case 'added': return e.added_date;
    case 'source': return e.source_date ?? '';
    case 'runs': return e.runs_since;
    // Best record first, and a horse with no runs sorts last rather than as a
    // 0% one — "not tested" is not the same as "tested and failed".
    case 'record': return e.runs_since ? e.wins_since / e.runs_since : -1;
    case 'next': return state.declared.has(e.horse_name) ? 1 : 0;
    case 'status': return e.status;
    default: return 0;
  }
}

/* ── the list ────────────────────────────────────────────────────────────── */

function renderHead() {
  $('bb-head').replaceChildren(...COLS.map((c) => {
    if (!c.label) return el('div', c.cls ?? null);
    const b = el('button', c.cls ?? null);
    b.append(document.createTextNode(c.label));
    if (state.sort === c.key) {
      b.setAttribute('aria-sort', state.sortDir > 0 ? 'ascending' : 'descending');
      b.append(el('span', 'ind', state.sortDir > 0 ? '▲' : '▼'));
    }
    b.addEventListener('click', () => {
      if (state.sort === c.key) state.sortDir *= -1;
      else { state.sort = c.key; state.sortDir = 1; }
      render();
    });
    return b;
  }));
}

function recordLabel(e) {
  if (!e.runs_since) return DASH;
  return `${e.wins_since}W-${e.places_since}P`;
}

function entryRow(e) {
  const open = state.open.has(e.id);
  const today = state.declared.has(e.horse_name);
  const row = el('div', `bb-row${open ? ' open' : ''}${today ? ' today' : ''}`);
  row.addEventListener('click', (event) => {
    if (event.target.closest('.acts')) return;
    toggleEntry(e);
  });

  row.append(el('div', 'caret', open ? '▼' : '▶'));

  const who = el('div', 'who');
  who.append(el('span', 'id', e.id.replace('bb_', '#')));
  who.append(el('span', 'nm', e.horse_name));
  row.append(who);

  const tags = el('div', 'tags');
  e.tags.forEach((t) => {
    const pill = el('span', 'tag-pill', t);
    pill.title = state.tagMeta?.[t] ?? 'no definition written for this tag';
    tags.append(pill);
  });
  row.append(tags);

  const booked = el('div', 'booked', e.added_date);
  const age = daysAgo(e.added_date);
  booked.title = age === null ? '' : `${age} days ago`;
  row.append(booked);

  const src = el('div', 'src', e.source_race || DASH);
  if (e.source_date_from === 'matched') {
    src.title = `date recovered from the horse's own runs: ${e.source_date}`;
    src.textContent = `${e.source_race} → ${e.source_date}`;
  }
  row.append(src);

  row.append(el('div', `runs${e.runs_since ? '' : ' none'}`,
    String(e.runs_since)));

  const rec = el('div', `record ${e.wins_since ? 'hit' : 'miss'}`, recordLabel(e));
  rec.title = e.runs_since
    ? `${e.wins_since} wins and ${e.places_since} places from ${e.runs_since} runs `
      + `since ${e.added_date}, derived from the runners table`
    : 'no runs since booking';
  row.append(rec);

  const next = el('div');
  next.append(el('span', today ? 'badge today' : 'badge quiet',
    today ? 'DECLARED' : (e.last_run ? `LAST ${e.last_run}` : 'NO RUNS')));
  row.append(next);

  const st = el('div');
  st.append(el('span', `badge ${e.status}`, e.status.replace('_', ' ').toUpperCase()));
  row.append(st);

  const acts = el('div', 'acts');
  if (e.review_due) {
    const r = el('span', 'review', `REVIEW · ${e.runs_since} RUNS UNRESOLVED`);
    r.title = 'four or more runs since booking with the thesis still open';
    acts.append(r);
  }
  if (e.status !== 'won_out') acts.append(statusButton(e, 'won_out', 'WON OUT'));
  if (e.status !== 'retired') acts.append(statusButton(e, 'retired', 'RETIRE', 'retire'));
  if (e.status === 'won_out' || e.status === 'retired') {
    acts.append(statusButton(e, 'active', 'REOPEN'));
  }
  row.append(acts);
  return row;
}

function statusButton(e, status, label, extra) {
  const b = el('button', `act-btn${extra ? ` ${extra}` : ''}`, label);
  b.disabled = state.busy.has(e.id);
  b.addEventListener('click', async (event) => {
    event.stopPropagation();
    state.busy.add(e.id);
    render();
    try {
      const out = await api.setBlackbookStatus(e.id, status);
      e.status = out.status;
      // The summary counts by status, so it has to be re-read, not patched.
      state.summary = await api.blackbookSummary(state.today);
    } catch (err) {
      b.textContent = err.message;
    } finally {
      state.busy.delete(e.id);
      render();
    }
  });
  return b;
}

function condLabel(r) {
  return [r.venue, r.course, r.distance ? `${r.distance}m` : null, r.going,
          r.race_class ? `C${r.race_class}` : null].filter(Boolean).join(' ');
}

function finClass(r) {
  if (r.place === 1) return 'win';
  if (r.place !== null && r.place <= 3) return 'placed';
  return 'unplaced';
}

function runLine(r, cls) {
  const line = el('div', `run-line${cls ? ` ${cls}` : ''}`);
  const when = el('div', 'when');
  when.append(document.createTextNode(`${r.race_date} `));
  when.append(el('span', 'cond', condLabel(r)));
  line.append(when);

  const st = el('div');
  st.append(el('span',
    `tag-pill ${styleClass(r.pace_style, { chip: false })}`,
    r.pace_style ?? 'UNKNOWN'));
  line.append(st);

  line.append(el('div', 'jockey', r.jockey ?? DASH));
  line.append(el('div', 'wt', r.win_odds ? num(r.win_odds, 1) : DASH));
  line.append(el('div', `fin ${finClass(r)}`,
    r.place === null || r.place === undefined
      ? (r.place_code ?? DASH) : `${r.place}`));

  const figs = el('div', 'figs');
  const figCls = r.et_figure === null || r.et_figure === undefined
    ? '' : r.et_figure >= 100 ? 'above' : 'below';
  figs.append(el('span', `fig ${figCls}`, r.et_figure === null
    || r.et_figure === undefined ? DASH : r.et_figure.toFixed(1)));
  figs.append(el('span', 'conf', r.et_confidence ?? ''));
  line.append(figs);

  const trail = el('div', 'trail');
  trail.append(el('span', 'pos', `${r.field_size ?? DASH} RAN`));
  trail.append(el('span', 'trip', r.placed ? 'placed' : ''));
  line.append(trail);
  return line;
}

function entryDetail(e) {
  const detail = state.details[e.id];
  const box = el('div', 'entry-detail');
  const main = el('div', 'main');

  const thesis = el('div', 'thesis');
  const cap = el('div', 'cap');
  cap.append(document.createTextNode('THE THESIS'));
  cap.append(el('span', 'meta',
    `BOOKED ${e.added_date}${e.source_race ? ` FROM ${e.source_race}` : ''}`
    + `${e.confidence ? ` · ${e.confidence.toUpperCase()} CONFIDENCE` : ''}`));
  thesis.append(cap);
  thesis.append(el('div', 'body', e.reasoning || 'no reason was recorded'));
  main.append(thesis);

  if (!detail) {
    main.append(el('div', 'empty-line', 'LOADING'));
    box.append(main);
    return box;
  }

  // The run the thesis was written from, on its own and NOT in the record
  // below. It is what the claim was made about, not a test of it — and in 71
  // of the 193 legacy entries with a source date it falls after the booking,
  // so folding it in credited the book with the run that inspired it.
  if (detail.source_run) {
    main.append(el('div', 'sub-cap', 'SOURCE RUN · IN FULL'));
    main.append(runLine(detail.source_run, 'source'));
  } else if (e.source_race) {
    main.append(el('div', 'sub-cap', 'SOURCE RUN · IN FULL'));
    main.append(el('div', 'empty-line',
      `${e.source_race} — NOT MATCHED TO A RUN ON RECORD`));
  }

  const since = detail.runs ?? [];
  const verdict = since.length
    ? `${detail.wins_since}W-${detail.places_since}P FROM ${since.length}`
    : 'NOT YET TESTED';
  const cap2 = el('div', 'sub-cap');
  cap2.append(document.createTextNode('EVERY RUN SINCE BOOKING · DID THE THESIS PLAY OUT'));
  cap2.append(el('span', 'aside', ` — ${verdict}`));
  main.append(cap2);

  if (!since.length) {
    main.append(el('div', 'empty-line',
      'NO RUNS SINCE — THE CLAIM HAS NOT BEEN TESTED YET'));
  } else {
    since.forEach((r) => main.append(runLine(r)));
  }
  box.append(main);

  const side = el('div', 'entry-side');
  side.append(el('div', 'sub-cap', 'HAND-WRITTEN NOTES ON THIS HORSE'));
  const written = detail.notes_written ?? [];
  if (!written.length) {
    side.append(el('div', 'empty-line',
      'NONE — THE RUNS ABOVE ARE DERIVED, NOT LOGGED'));
  } else {
    written.forEach((n) => {
      const row = el('div', 'note-row');
      row.append(el('span', 'd', n.race_date));
      row.append(el('span', 'v', n.finish ?? DASH));
      if (n.verdict) row.append(el('span', `verdict verdict-${n.verdict}`, n.verdict));
      const txt = el('span', 'txt', n.notes ?? '');
      txt.title = n.notes ?? '';
      row.append(txt);
      side.append(row);
    });
  }

  // Brief 06 calls missed bets "the single most important feature", so a run
  // with no ticket against it stays in the timeline marked NOT BACKED rather
  // than being dropped — a missing row would read as "nothing was missed".
  // Real money, run by run, with the balance carried forward. Not a notional
  // flat stake: one run here attracted eight tickets at four different sizes,
  // so a fixed-stake figure would describe a bet that was never placed.
  const money = detail.totals;
  side.append(el('div', 'sub-cap', 'BETS SINCE BOOKING · RUNNING BALANCE'));

  if (!money) {
    side.append(el('div', 'empty-line', 'LOADING'));
  } else if (!money.runs) {
    side.append(el('div', 'empty-line', 'NO RUNS SINCE BOOKING'));
  } else {
    (detail.runs_since ?? []).forEach((r) => {
      const row = el('div',
        `bet-run${r.backed ? '' : ' missed'}${r.is_source ? ' source' : ''}`);
      if (r.is_source) {
        row.title = 'The run this entry was written from. It does not test the '
          + 'thesis, so the record above leaves it out — but the money on it '
          + 'was real.';
      }
      const head = el('div', 'line');
      head.append(el('span', 'd', r.race_date));
      head.append(el('span', 'pl', `R${r.race_no} · ${r.place ?? r.place_code ?? DASH}`));
      head.append(el('span', 'sp', r.win_odds ? num(r.win_odds, 1) : DASH));
      if (r.backed) {
        head.append(el('span', 'stake', `$${r.staked.toLocaleString()}`));
        head.append(el('span', `pnl ${r.pnl >= 0 ? 'pos' : 'neg'}`,
          `${r.pnl >= 0 ? '+' : ''}${Math.round(r.pnl)}`));
      } else {
        // A missed chance is a run with no money against it. Shown in the same
        // timeline, not as a counterfactual — no bet was placed, so there is
        // no return to claim.
        head.append(el('span', 'not-backed', 'NOT BACKED'));
      }
      head.append(el('span', `bal ${r.balance >= 0 ? 'pos' : 'neg'}`,
        `${r.balance >= 0 ? '+' : ''}${Math.round(r.balance)}`));
      row.append(head);

      if (r.backed) {
        const tickets = el('div', 'tickets');
        r.bets.forEach((b) => {
          const multi = (b.legs ?? 1) > 1;
          const t = el('span', `ticket${multi ? ' multi' : ''}`);
          t.append(el('span', 'ty', b.bet_type.replace('_BANKER', '·B')));
          t.append(el('span', null, `$${b.stake}`));
          if (multi) t.append(el('span', 'legs', `${b.legs}L`));
          if (b.returned > 0) t.append(el('span', 'won', `→$${Math.round(b.returned)}`));
          t.title = `${b.bet_type}${b.is_banker ? ' · banker' : ''}`
            + (multi ? ` · ${b.legs}-leg ticket, whole stake shown here` : '')
            + ` · staked $${b.stake} · returned $${b.returned ?? 0}`;
          tickets.append(t);
        });
        row.append(tickets);
      }
      side.append(row);
    });
  }

  const ledger = el('div', 'ledger-box');
  if (money) {
    const row = (k, v, cls) => {
      const line = el('div', 'row');
      line.append(el('span', 'k', k));
      line.append(el('span', 'v', v));
      if (cls) line.append(el('span', `n ${cls[0]}`, cls[1]));
      ledger.append(line);
    };
    row('STAKED', `$${money.staked.toLocaleString()} over ${money.bets} tickets`);
    row('RETURNED', `$${money.returned.toLocaleString()}`,
      money.roi === null ? null
        : [money.pnl >= 0 ? 'pos' : 'neg',
           `${money.pnl >= 0 ? '+' : MINUS}$${Math.abs(Math.round(money.pnl)).toLocaleString()}`]);
    row('RUNS', `${money.backed_runs} backed · ${money.missed_runs} not backed`);
    if (money.roi !== null) {
      ledger.append(el('div', 'caveat',
        `ROI ${signedPct(money.roi)} on money actually staked — not a notional `
        + 'flat bet on every run.'));
    }
    if (money.multi_leg_bets) {
      ledger.append(el('div', 'caveat',
        `${money.multi_leg_bets} of these are all-up tickets spanning other `
        + 'races. An all-up cannot be split between its legs, so the whole '
        + 'stake is counted here — the money was riding on other horses too.'));
    }
    if (detail.unmatched_bets) {
      ledger.append(el('div', 'caveat',
        `${detail.unmatched_bets} bet(s) on this horse fall outside the runs `
        + 'above and are not counted here.'));
    }
  }
  side.append(ledger);
  box.append(side);
  return box;
}

function renderList() {
  renderChips();
  renderHead();

  const shown = state.entries.filter(matches).sort((a, b) => {
    const x = sortValue(a, state.sort);
    const y = sortValue(b, state.sort);
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return c * state.sortDir;
  });
  renderActiveFilters(shown.length);

  const host = $('entries');
  if (!shown.length) {
    const box = el('div', 'no-match');
    box.append(document.createTextNode(
      state.entries.length ? 'NOTHING MATCHES THIS FILTER SET.'
        : 'THE BOOK IS EMPTY.'));
    box.append(el('br'));
    box.append(el('span', 'hint', state.entries.length
      ? 'Filters combine, so narrowing on tag and status and date together can '
        + 'easily reach zero. Remove a chip above.'
      : 'Import one with: python -m hkrd.jobs.import_blackbook --src blackbook.json'));
    host.replaceChildren(box);
  } else {
    const rows = [];
    shown.forEach((e) => {
      rows.push(entryRow(e));
      if (state.open.has(e.id)) rows.push(entryDetail(e));
    });
    host.replaceChildren(...rows);
  }

  const foot = $('bb-foot');
  foot.replaceChildren();
  foot.append(el('span', null,
    'CLICK AN ENTRY FOR THE THESIS AND EVERY RUN SINCE — DERIVED FROM THE '
    + 'RUNNERS TABLE, NOT FROM WHAT WAS LOGGED'));
  foot.append(el('span', 'amber', '■ DECLARED AT THE LATEST MEETING'));
  foot.append(el('span', 'magenta', 'REVIEW PROMPT AT 4+ RUNS UNRESOLVED'));
  foot.append(el('span', 'right',
    'RETIRING IS ONE CLICK — A BOOK THAT ONLY GROWS IS UNUSABLE WITHIN A SEASON'));
}

/* ── analysis ────────────────────────────────────────────────────────────── */

/** The A/E interval, drawn against a fixed 0–3 scale with the tick at 1.00.
 *  A per-row scale would make every interval look the same width. */
function aeCell(t) {
  const cell = el('div', 'ae-cell');
  if (t.ae === null) {
    cell.append(el('span', 'v', DASH));
    cell.append(el('span', 'lab', 'no priced runs'));
    return cell;
  }
  const clears = t.ae_lo > 1 || t.ae_hi < 1;
  cell.append(el('span', `v${clears ? ' pos' : ''}`, t.ae.toFixed(2)));

  const track = el('div', 'track');
  track.append(el('div', 'tick'));
  const scale = (v) => Math.max(0, Math.min(100, v / 2 * 100));
  const ci = el('div', `ci${clears ? ' clears' : ''}`);
  ci.style.left = `${scale(t.ae_lo)}%`;
  ci.style.width = `${Math.max(1, scale(t.ae_hi) - scale(t.ae_lo))}%`;
  track.append(ci);
  const mark = el('div', 'mark');
  mark.style.left = `${scale(t.ae)}%`;
  track.append(mark);
  cell.append(track);

  cell.append(el('span', 'lab', `${t.ae_lo}–${t.ae_hi}`));
  cell.title = `${t.wins} wins against ${t.expected_wins} the market implied over `
    + `${t.ae_runs} fully priced runs · scale is 0 to 2, tick at 1.00`;
  return cell;
}

/** One tag's backed-versus-missed reading, or a why-not when it has none. */
function tagBackedVsMissed(d) {
  const box = el('div', 'bvm');
  if (!d || !d.runs) {
    box.append(el('span', 'none', 'NO RUNS SINCE BOOKING'));
    return box;
  }
  const side = (roi, runs, cls) => {
    const s = el('span', cls);
    // "36 runs · −15.0%". Without the unit and the separator the two numbers
    // ran together and read as one — "36-15.0%".
    s.append(el('span', 'n', `${runs} run${runs === 1 ? '' : 's'}`));
    s.append(el('span', 'v', roi === null || roi === undefined
      ? DASH : signedPct(roi)));
    return s;
  };
  box.append(side(d.backed_roi, d.backed_runs, 'backed'));
  box.append(el('span', 'v-sep', 'v'));
  box.append(side(d.missed_roi, d.missed_runs, 'missed'));
  // The gap is the finding. A tag whose MISSED side outperforms is one the
  // book is right about and the ledger is not — brief 06's whole argument,
  // read one reason at a time.
  if (d.gap !== null && d.gap !== undefined) {
    const note = el('span', `gap ${d.gap >= 0 ? 'pos' : 'neg'}`,
      d.gap >= 0 ? 'backed ahead' : 'missed ahead');
    note.title = `${signedPct(d.gap)} between the two sides`;
    box.append(note);
  }
  box.title = d.verdict ?? '';
  return box;
}

function renderAnalysis() {
  const meta = state.tagMeta ?? {};
  const rows = [...state.tags].sort((a, b) => b.runs - a.runs);
  $('analysis-sub').textContent =
    `EVERY FIGURE CARRIES ITS SAMPLE · UNDER ${THIN_RUNS} RUNS A TAG IS DIMMED `
    + 'AND SHOULD NOT BE READ AS SIGNAL';

  $('tag-rows').replaceChildren(...rows.map((t) => {
    const row = el('div', `tag-row${t.thin ? ' thin' : ''}`);
    const label = el('div', 'label', t.tag);
    label.title = t.definition ?? 'no definition written for this tag';
    row.append(label);
    row.append(el('div', 'r n', String(t.entries_booked)));
    row.append(el('div', 'r n', String(t.runs)));

    const sr = el('div', 'r');
    sr.append(el('span', 'rate', pct(t.strike_rate, 1)));
    sr.append(el('span', 'of', ` ${t.wins}/${t.runs}`));
    row.append(sr);

    const pr = el('div', 'r');
    pr.append(el('span', null, pct(t.place_rate, 1)));
    pr.append(el('span', 'of', ` ${t.places}/${t.runs}`));
    row.append(pr);

    row.append(aeCell(t));

    const roi = el('div', 'r roi');
    roi.textContent = signedPct(t.roi_win);
    if (t.roi_win !== null) roi.classList.add(t.roi_win >= 0 ? 'pos' : 'neg');
    roi.title = `${t.priced_runs} runs carried a price`;
    row.append(roi);

    // BACKED vs MISSED per booking reason — the artboard's own last column.
    // "Runs I booked for trip trouble and then did not back" is a sharper
    // question than the same one over the whole book, because it names the
    // reason the entry was made. The definition has not been dropped: it is
    // the tag label's hover, two columns to the left, where it always was.
    row.append(tagBackedVsMissed(state.tagBvm?.[t.tag]));
    return row;
  }));

  const foot = $('analysis-foot');
  foot.replaceChildren();
  foot.append(el('span', null,
    'A/E = ACTUAL WINS ÷ WINS THE MARKET IMPLIED. 1.00 IS THE MARKET; '
    + 'THE TICK IS 1.00 AND THE BAR IS THE 95% INTERVAL'));
  const cleared = state.tagMeta ? state.cleared : [];
  foot.append(el('span', cleared?.length ? 'pos' : null,
    `${cleared?.length ?? 0} OF ${state.scored ?? 0} TAGS CLEAR 1.00 AT 95% — `
    + `${state.expectedByChance ?? 0} WOULD BY CHANCE`));
  foot.append(el('span', 'right',
    "A TAG THAT LOOKS LIKE IT'S WORKING ON SIX ENTRIES PROBABLY ISN'T"));

  renderWholeBook();
  renderStatusPanel();
}

/* The account this comparison is being asked about.
 *
 * BOTH is the book's own hit rate, independent of which wallet paid. A single
 * account is that account's selection discipline: a run the OTHER book took
 * counts as missed here, which is the honest reading and the whole reason the
 * split is worth having.
 *
 * The two readings must never be summed. A run Kelvin took and Brett did not
 * is missed-for-Brett and backed-overall; both are true, and adding them
 * breaks the invariant the page rests on — backed + missed equals runs since.
 */
const ACCOUNTS = [
  ['', 'BOTH', 'the book itself, whoever paid'],
  ['brett', 'BRETT', 'backed on Brett; a Kelvin-only run counts as missed'],
  ['kelvin', 'KELVIN', 'backed on Kelvin; a Brett-only run counts as missed'],
];

function accountSwitch() {
  const bar = el('div', `bm-acct${state.account ? ` acct-${state.account}` : ''}`);
  bar.append(el('span', 'lab', 'LEDGER'));
  ACCOUNTS.forEach(([key, label, why]) => {
    const on = (state.account ?? '') === key;
    const b = el('button', `bm-chip${on ? ' on' : ''}`, label);
    b.type = 'button';
    b.title = why;
    b.setAttribute('aria-pressed', String(on));
    b.addEventListener('click', async () => {
      state.account = key || null;
      state.backedMissed = null;
      renderWholeBook();
      state.backedMissed = await api.backedVsMissed(null, state.account);
      renderWholeBook();
    });
    bar.append(b);
  });
  const split = state.backedMissed?.by_account ?? {};
  if (Object.keys(split).length) {
    // Counted per account, never summed: a run both books took is one run and
    // appears in both counts.
    bar.append(el('span', 'split', Object.entries(split)
      .map(([a, n]) => `${a} ${n}`).join(' · ')));
  }
  return bar;
}

function renderWholeBook() {
  const s = state.summary;
  const bm = state.backedMissed;
  const host = $('whole-book');
  host.replaceChildren();
  if (!s) return;

  const metric = (label, n, value, roi) => {
    const box = el('div', 'metric');
    const line = el('div', 'line');
    line.append(el('span', 'label', label));
    line.append(el('span', 'n', `n=${n}`));
    line.append(el('span', 'v', value));
    if (roi !== null && roi !== undefined) {
      line.append(el('span', `big ${roi >= 0 ? 'pos' : 'neg'}`, signedPct(roi)));
    }
    box.append(line);
    const bar = el('div', 'bar');
    const fill = el('i', roi === null || roi === undefined ? 'flat'
      : roi >= 0 ? 'pos' : 'neg');
    // ±50% fills the bar; beyond that it saturates rather than misleading.
    fill.style.width = `${Math.min(100, Math.abs(roi ?? 0) * 200)}%`;
    bar.append(fill);
    box.append(bar);
    return box;
  };

  if (!bm) {
    host.append(metric('EVERY RUN SINCE, FLAT $1 WIN', s.runs_since,
      `${s.wins_since} wins`, s.flat_roi));
    host.append(el('div', 'closing', 'LOADING THE BETS LEDGER'));
    return;
  }

  // ONE BOOK, TWO LEDGERS. The book is shared — a horse is followed for what
  // it did, not for whose money is on it — but "was this run backed" has a
  // different answer per account, and the gap between them is a finding about
  // each book's discipline rather than about the horses.
  host.append(accountSwitch());

  // Both sides priced at the SAME notional flat win stake. The real ledger is
  // quinellas and multi-leg tickets; comparing those against a notional win
  // bet would measure the bet type, not the selection.
  ['backed', 'missed'].forEach((side) => {
    const d = bm[side];
    host.append(metric(side.toUpperCase(), d.runs,
      `${d.wins} wins · strike ${pct(d.strike_rate)}`, d.roi));
  });

  // Strike rate beside the chance the PRICE implied. This is the row that
  // says whether the selection did anything the board did not.
  const table = el('div', 'implied');
  ['backed', 'missed'].forEach((side) => {
    const d = bm[side];
    if (d.implied_rate === null) return;
    const row = el('div', 'implied-row');
    row.append(el('span', 'k', side.toUpperCase()));
    row.append(el('span', null, `median ${num(d.median_odds, 1)}`));
    row.append(el('span', null, `implied ${pct(d.implied_rate)}`));
    row.append(el('span', null, `actual ${pct(d.strike_rate)}`));
    const edge = el('span', 'edge', signedPct(d.vs_implied));
    // Within a point of the implied rate is the market's own number, not an
    // edge — so it is not coloured as one.
    if (Math.abs(d.vs_implied) > 0.01) edge.classList.add(d.vs_implied > 0 ? 'pos' : 'neg');
    row.append(edge);
    table.append(row);
  });
  host.append(table);

  const a = bm.actual;
  if (a.bets) {
    host.append(metric('WHAT WAS ACTUALLY STAKED ON THEM', a.bets,
      `$${a.staked.toLocaleString()} → $${a.returned.toLocaleString()}`, a.roi));
  }

  const backed = bm.backed;
  const missed = bm.missed;
  host.append(el('div', 'closing warn',
    `${backed.runs} of ${bm.runs} runs since booking were backed and `
    + `${missed.runs} were not — detected from the ledger, not logged by hand. `
    + `The backed side strikes ${pct(backed.strike_rate)} at a median `
    + `${num(backed.median_odds, 1)}, the missed side ${pct(missed.strike_rate)} `
    + `at ${num(missed.median_odds, 1)}. Both land within a point of what their `
    + `price implied, so the gap between those strike rates is the odds, not `
    + `the picking.`));
}

function renderStatusPanel() {
  const s = state.summary;
  const host = $('status-panel');
  host.replaceChildren();
  if (!s) return;

  const total = s.total || 1;
  const order = [['active', 'ACTIVE'], ['expired', 'EXPIRED'],
                 ['won_out', 'WON OUT'], ['retired', 'RETIRED']];
  order.forEach(([key, label]) => {
    const n = s.status[key] ?? 0;
    const row = el('div', 'status-row');
    row.append(el('span', 'label', label));
    const track = el('div', 'track');
    const fill = el('i');
    fill.style.width = `${100 * n / total}%`;
    fill.style.background = key === 'active' ? 'var(--book)'
      : key === 'won_out' ? 'var(--win)'
        : key === 'retired' ? 'var(--loss)' : 'var(--text-faint)';
    track.append(fill);
    row.append(track);
    row.append(el('span', 'n', String(n)));
    row.append(el('span', 'note', `${(100 * n / total).toFixed(0)}%`));
    host.append(row);
  });

  // Expiry is a timer running out, not a judgement. Counting it as resolution
  // would make a book nobody ever reviewed look healthy.
  const judged = (s.status.won_out ?? 0) + (s.status.retired ?? 0);
  host.append(el('div', 'closing',
    `${judged} of ${s.total} entries were resolved by a judgement; `
    + `${s.status.expired ?? 0} simply expired, which is a timer running out `
    + `rather than a verdict. ${s.review_due} active entries have four or more `
    + 'runs since booking and are waiting on one.'));
}

/* ── loading ─────────────────────────────────────────────────────────────── */

function render() {
  renderViewToggle();
  renderSummary();
  $('view-list').hidden = state.view !== 'list';
  $('view-analysis').hidden = state.view !== 'analysis';
  if (state.view === 'list') renderList();
  else renderAnalysis();
}

async function toggleEntry(e) {
  if (state.open.has(e.id)) {
    state.open.delete(e.id);
    render();
    return;
  }
  state.open.add(e.id);
  render();
  if (!state.details[e.id]) {
    try {
      state.details[e.id] = await api.blackbookEntry(e.id);
    } catch {
      state.details[e.id] = { runs: [], notes_written: [] };
    }
    render();
  }
}

async function init() {
  renderNav($('nav'), 'blackbook.html');
  renderViewToggle();

  $('search').addEventListener('input', (e) => {
    state.search = e.target.value.trim();
    render();
  });
  $('clear-search').addEventListener('click', () => {
    state.search = '';
    $('search').value = '';
    render();
  });

  installPalette();
  context.onChange(() => { state.today = context.date; render(); });
  await context.init();
  // The book is meeting-independent, but "running today" is not — it follows
  // whichever meeting Layer 1 is on.
  const latest = context.date;
  state.today = latest;

  try {
    const [list, tags, summary, declared, backedMissed, tagBvm]
      = await Promise.all([
      api.blackbook(),
      api.blackbookTags(),
      api.blackbookSummary(latest),
      // A meeting with nothing booked is normal, and a missing meeting must
              // not take the whole page down with it.
              latest ? api.blackbookDeclared(latest).catch(() => ({ entries: [] }))
        : Promise.resolve({ entries: [] }),
      api.backedVsMissed().catch(() => null),
      // A per-tag pass over the ledger. Failing it must not take the page
      // down: the column reads "no runs since booking" and everything else
      // on the analysis view still renders.
      api.tagsBackedVsMissed().catch(() => ({ tags: {} })),
    ]);
    state.entries = list.entries;
    state.tags = tags.tags;
    state.cleared = tags.cleared;
    state.scored = tags.scored;
    state.expectedByChance = tags.expected_by_chance;
    state.tagMeta = Object.fromEntries(
      tags.tags.map((t) => [t.tag, t.definition]).filter(([, d]) => d));
    state.summary = summary;
    state.backedMissed = backedMissed;
    state.tagBvm = tagBvm?.tags ?? {};
    state.declared = new Set(declared.entries.map((d) => d.horse_name));
  } catch (e) {
    $('entries').replaceChildren(el('div', 'no-match', `failed to load: ${e.message}`));
    return;
  }
  render();
}

init();
