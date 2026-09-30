/* briefing.js — the meeting Briefing, the dashboard's front page.
 *
 * Ported from web/design-source/Briefing.dc.html (Claude Design, 25 Sep 2026)
 * and fed by one endpoint, GET /api/briefing/{date} (query/briefing): per race,
 * what the dashboard computes, what people said and what the markets price,
 * kept apart and never added together.
 *
 * It follows the day. Two days out it is the card and the Screen, with what
 * is due and when; the night before the voices land; on race day it re-reads
 * every minute until the last race, and a race that has run collapses to its
 * winner and opens to the Screen beside where each horse finished. Below it,
 * every source's record this season (briefing-record). It is a briefing, not
 * a bet slip, and it re-ranks nothing.
 */
import { api } from './api.js';
import { context } from './context.js';
import { install as installPalette } from './palette.js';
import { $, el, renderNav } from './vocab.js';
import { buildView } from './briefing-model.js';
import { renderDesk, renderPanels } from './briefing-desk.js';
import { renderPhone } from './briefing-phone.js';
import { renderRecord } from './briefing-record.js';
import { renderBackgroundRecord } from './briefing-background.js';

const LIVE_EVERY = 60e3;          // race day, a race still to run
const QUIET_EVERY = 10 * 60e3;    // otherwise: sources land on a clock of hours

const params = new URLSearchParams(window.location.search);
const firstRace = Number(params.get('race')) || null;
// ?as_of=2026-09-23T17:40 reads the page as it stood then; a page pinned to a
// moment does not re-read itself.
const asOf = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(params.get('as_of') ?? '') ? params.get('as_of') : null;
const state = { data: null, error: null, date: null, timer: null, ui: fresh(),
                record: null, recordRan: -1 };

/** `urlRace`: the race the address named, which the page does not open once
 *  it has run (briefing-model, buildView). A click clears it. */
function fresh(race = null) {
  return { openD: race ?? undefined, openM: race ?? undefined, urlRace: race,
           openR: {}, openRM: {} };
}

/** The sources' record changes only when a result lands, so it is read on
 *  the first load and again only when another race has run. */
async function loadRecord() {
  const ran = state.data ? state.data.races.filter((r) => r.run).length : 0;
  if (ran === state.recordRan) return;
  state.recordRan = ran;
  // Both records at once: they answer different questions and neither waits
  // on the other.
  const bgRead = api.backgroundRecord().catch((e) => (
    { error: `The background record could not be read — ${e.message}` }));
  try {
    state.record = await api.tipsRecord();
  } catch (e) {
    state.record = { error: `The sources' record could not be read — ${e.message}` };
  }
  renderRecord($('bf-record'), state.record);
  renderBackgroundRecord($('bf-bgrec'), await bgRead);
}

/** The first read, asked for as the page opens when the URL names the date
 *  (main): it then travels beside the page's context instead of behind it. */
let early = null;

async function load({ quiet = false } = {}) {
  const date = context.date;
  if (date !== state.date) {
    // Race 9 of one card is not race 9 of another: a new meeting opens fresh.
    state.date = date;
    state.data = null;
    state.ui = fresh();
  }
  if (!date) { render(); return; }
  if (!quiet) { state.error = null; render(); }
  try {
    const first = early && early.date === date ? await early.read : null;
    early = null;
    const got = first ?? await api.briefing(date, asOf);
    if (context.date !== date) return;          // the meeting changed meanwhile
    state.data = got;
    state.error = null;
  } catch (e) {
    // A failed re-read keeps the last honest reading on screen and says so;
    // a failed first read says what failed rather than showing an empty card.
    state.error = `The briefing could not be read — ${e.message}`;
  }
  render();
  schedule();
  loadRecord();
}

function schedule() {
  clearTimeout(state.timer);
  const d = state.data;
  if (!d || asOf) return;
  const live = d.stage === 'race_day' && d.races.some((r) => !r.run);
  state.timer = setTimeout(() => {
    if (document.hidden) { schedule(); return; }
    load({ quiet: true });
  }, live ? LIVE_EVERY : QUIET_EVERY);
}

/* ── what a click does ──────────────────────────────────────────────────── */

const act = {
  race(no) {
    const view = buildView(state.data, state.ui);
    state.ui.urlRace = null;
    state.ui.openD = view.openD === no ? null : no;
    if (state.ui.openD) context.setRace(no);
    render();
  },
  raceM(no) {
    const view = buildView(state.data, state.ui);
    state.ui.urlRace = null;
    state.ui.openM = view.openM === no ? null : no;
    render();
  },
  runner(race, horse) { state.ui.openR[race] = horse; render(); },
  runnerM(race, horse) { state.ui.openRM[race] = horse; render(); },
  edge(race, horse) {
    state.ui.urlRace = null;
    state.ui.openD = race;
    state.ui.openM = race;
    state.ui.openR[race] = horse;
    render();
    document.querySelector(`#bf-desk .bf-race[data-race="${race}"]`)
      ?.scrollIntoView({ block: 'start' });
  },
};

/* ── the page ───────────────────────────────────────────────────────────── */

function renderBar(view) {
  const bar = $('bf-bar');
  bar.replaceChildren(el('div', 'bf-title', 'BRIEFING'));
  if (!view) return;
  const clk = el('div', 'bf-clock');
  view.clock.forEach((c) => {
    const s = el('span', 'item');
    s.title = `${c.label}${c.counted ? ` (${c.counted})` : ''}: ${c.detail}`;
    s.append(el('span', `ab a-${c.abTone}`, c.ab), el('span', `t-${c.tone}`, c.txt));
    clk.append(s);
  });
  const cap = el('div', 'bf-cap');
  view.capLine.forEach((k) => {
    const s = el('span', null, `${k.l} `);
    s.append(el('span', `v t-${k.tone || 'plain'}`, k.v));
    cap.append(s);
  });
  bar.append(clk, cap);
  // The phone's version of the same strip: a mark per source, and what is next.
  const ph = el('div', 'bf-pbar');
  const top = el('div', 'top');
  top.append(el('span', 'venue', `${view.venue} ${view.dateShort}`), el('span', 'asof', view.asOfShort));
  const marks = el('div', 'marks');
  view.clock.forEach((c) => {
    const s = el('span', `a-${c.abTone}`, c.ab);
    s.append(el('span', `t-${c.tone}`, ` ${c.mark}`));
    marks.append(s);
  });
  ph.append(top, marks);
  if (view.nextDue) ph.append(el('div', 'next', view.nextDue));
  bar.append(ph);
}

function renderNow(view) {
  const host = $('bf-now');
  host.replaceChildren();
  host.hidden = !view || !view.nowItems.length;
  if (host.hidden) return;
  view.nowItems.forEach((n) => {
    const s = el('span', `item${n.go ? ' go' : ''}`);
    if (n.go) s.addEventListener('click', () => act.edge(n.go, undefined));
    s.append(el('span', 'l', n.l), el('span', `v${n.stale ? ' stale' : n.dim ? ' dim' : ''}`, n.v),
             el('span', 'sub', n.sub));
    host.append(s);
  });
}

function render() {
  const d = state.data;
  const status = $('bf-status');
  status.hidden = !state.error && !!d;
  status.textContent = state.error
    ?? (!context.date ? 'No meeting chosen.' : 'Reading the card…');
  if (!d || !d.races) {
    renderBar(null);
    renderNow(null);
    ['bf-desk', 'bf-phone', 'bf-panels'].forEach((id) => $(id).replaceChildren());
    return;
  }
  const view = buildView(d, state.ui);
  $('bf-stage').textContent = view.stageLabel;
  renderBar(view);
  renderNow(view);
  const ctx = { date: d.race_date, act };
  renderDesk($('bf-desk'), view, ctx);
  renderPhone($('bf-phone'), view, ctx);
  renderPanels($('bf-panels'), view, ctx);
  $('bf-fit').textContent = view.fitLine;
}

async function main() {
  // The meeting in the header opens the palette on MEETINGS, and so does
  // ⌘K; both only send an event, and without this nothing here heard it.
  installPalette();
  const named = params.get('date');
  if (/^\d{4}-\d{2}-\d{2}$/.test(named ?? '')) {
    early = { date: named, read: api.briefing(named, asOf).catch(() => null) };
  }
  await context.init();
  renderNav($('nav'), 'briefing.html');
  state.ui = fresh(firstRace);
  state.date = context.date;
  context.onChange((_c, what) => {
    if (what === 'date' || what === 'meeting') load();
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && state.data) load({ quiet: true });
  });
  await load();
}

main();
