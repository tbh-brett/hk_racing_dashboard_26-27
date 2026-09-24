/* briefing-screen.js — the Screen: who is a chance, before the price.
 *
 * The Briefing's first section. One row per race: the pace shape, the four
 * horses the Screen would read first with their chance to place, anything else
 * with a case, and the blackbook horses with whether today's set-up suits
 * them. A race opens to every runner: the measured reasons for and against,
 * the last start in HKJC's words, the latest trial, the owner's own notes,
 * and the horses in the field it could turn around.
 *
 * Every number is the server's (`/api/screen/{date}`, model/screen). The
 * browser formats and never re-scores: a second scoring here would drift from
 * the one the weights were fitted on and neither would look wrong.
 *
 * A chip's multiplier is on the horse's chance ON TOP of its form rating,
 * measured 2020-21 to now. The page says what the Screen is not, beside what
 * it is: where it rates a horse well above the tote, the tote has been right.
 */
import {
  DASH, el, styleBadge, compactDate, ordinal, drawText, classLabel,
} from './vocab.js';

const state = { open: null, weights: false };
let last = null;

// A chip needs a word, not a sentence; the sentence is its tooltip.
const SHORT = {
  debut: 'DEBUT', first_up: '1ST-UP', second_up_bad: '2ND-UP AFTER BAD',
  deep_campaign: 'RUN 5+', prev_beaten: 'BEATEN LAST', prev_excuse_beaten: 'EXCUSE',
  prev_wide: 'WIDE LAST', prev_vet: 'VET', leader_alone: 'LONE LEADER',
  leader_pair: 'LEADER OF 2', leader_crowd: 'LEADER OF 3+', on_pace: 'ON PACE',
  closer: 'CLOSER', trial_good: 'TRIAL +', trial_bad: 'TRIAL −', rating_up: 'RTG UP',
  rating_down: 'RTG DOWN', class_rise: 'CLASS UP', draw_in: 'DRAW IN',
  draw_out: 'DRAW OUT', venue_change: 'OTHER COURSE', trainer_change: 'NEW STABLE',
};
const pct = (v) => (v === null || v === undefined ? DASH : `${Math.round(v)}%`);
const times = (x) => `×${x.toFixed(2)}`;
const NL = String.fromCharCode(10);   // a line break in a tooltip

/** Draw the Screen into `host`. `scr` is the payload, `{error}`, or null
 *  while loading; `tips` is race_no -> Map(horse_no -> sources backing). */
export function renderScreen(host, scr, tips) {
  last = { host, scr, tips: tips ?? new Map() };
  draw();
}

function draw() {
  const { host, scr, tips } = last;
  host.replaceChildren(head(scr));
  if (!scr) { host.append(el('div', 'scr-empty', 'Reading the card…')); return; }
  if (scr.error) { host.append(el('div', 'scr-empty', scr.error)); return; }
  host.append(columns());
  scr.races.forEach((r) => {
    const wrap = el('div', `scr-race${r.run ? ' ran' : ''}`);
    wrap.append(row(r, tips.get(r.race_no)));
    if (state.open === r.race_no) wrap.append(detail(r, tips.get(r.race_no)));
    host.append(wrap);
  });
  host.append(foot(scr));
}

function toggle(no) {
  state.open = state.open === no ? null : no;
  draw();
}

/* ── the section head ───────────────────────────────────────────────────── */

function head(scr) {
  const h = el('div', 'scr-head');
  h.append(el('div', 'scr-title', 'SCREEN'),
           el('div', 'scr-sub', 'who is a chance · read before the price'));
  const f = scr?.fit;
  if (f?.top4_has_winner?.screen) {
    const t = el('div', 'scr-proof');
    const w = f.top4_has_winner;
    t.append(el('span', 'dim', `TOP ${f.shortlist} HELD THE WINNER `),
             el('b', null, pct(100 * w.screen)),
             el('span', 'dim', ` · FORM ALONE ${pct(100 * w.form)} · CLOSING TOTE `
               + `${pct(100 * w.market)} · ${f.test_races.toLocaleString()} TEST RACES`));
    h.append(t);
  }
  return h;
}

function columns() {
  const c = el('div', 'scr-cols');
  ['R', 'RACE', 'PACE', 'SHORTLIST · CHANCE TO PLACE', 'ALSO A CASE', 'YOUR BOOK · SET-UP']
    .forEach((t) => c.append(el('div', null, t)));
  return c;
}

/* ── one race, closed ───────────────────────────────────────────────────── */

function nameEl(r, tipsHere, cls = 'nm') {
  const n = el('span', `${cls}${r.blackbook?.live ? ' booked' : ''}`,
               `${r.horse_no} ${r.horse_name}`);
  const box = el('span', 'scr-name');
  box.append(n);
  const k = tipsHere?.get(r.horse_no);
  if (k) {
    const t = el('span', 'scr-tips', `TIPS ${k}`);
    t.title = `${k} source${k === 1 ? '' : 's'} back this horse — see the board below`;
    box.append(t);
  }
  return box;
}

function resultEl(r) {
  if (r.result === null || r.result === undefined) return null;
  return el('span', `scr-res${r.result <= 3 ? ' in' : ''}`, ordinal(r.result));
}

function paceCell(race) {
  const p = race.pace;
  const c = el('div', 'c-pace');
  const n = p.leaders.length;
  const line = el('div', 'lead');
  if (!n) line.append(el('span', 'dim', 'NO HABITUAL LEADER'));
  else {
    line.append(el('span', 'k', n === 1 ? 'LONE LEADER ' : `${n} LEADERS `),
                el('span', null, p.leaders.map((x) => `#${x.horse_no}`).join(' ')));
    const x = el('span', `x ${p.leader_x >= 1.1 ? 'for' : 'flat'}`, ` ${times(p.leader_x)}`);
    x.title = 'what being a habitual leader has been worth with this many of them';
    line.append(x);
  }
  const k = p.counts;
  c.append(line, el('div', 'dim',
    `L ${k.Leader} · P ${k['On-Pace']} · M ${k.Midfield} · C ${k.Closer}`
    + (p.unknown ? ` · ? ${p.unknown}` : '')));
  return c;
}

function row(race, tipsHere) {
  const open = state.open === race.race_no;
  const r = el('div', `scr-row${open ? ' open' : ''}`);
  r.addEventListener('click', () => toggle(race.race_no));
  const cell = (cls, ...kids) => { const c = el('div', cls); c.append(...kids); return c; };
  r.append(cell('c-r', el('div', 'no', `R${race.race_no}`),
                el('div', 'off', race.run ? 'RAN' : race.off_time ?? '')));
  r.append(cell('c-race', el('div', null, `${race.distance ?? DASH}m · ${classLabel(race.race_class) ?? DASH}`),
                el('div', 'dim', `${race.venue ?? ''} ${race.course ?? ''} · ${race.field_size}`)));
  r.append(paceCell(race));

  const short = cell('c-short');
  race.runners.filter((x) => x.tier === 'SHORTLIST').forEach((x) => {
    const it = el('div', 'pick');
    it.append(nameEl(x, tipsHere), el('span', 'pp', pct(x.place_pct)));
    const res = resultEl(x);
    if (res) it.append(res);
    short.append(it);
  });
  r.append(short);

  const cases = cell('c-case', el('div', 'lbl', 'ALSO A CASE'));
  const also = race.runners.filter((x) => x.tier === 'CASE');
  if (!also.length) { cases.classList.add('none'); cases.append(el('span', 'dim', DASH)); }
  also.slice(0, 3).forEach((x) => {
    const it = el('div', 'pick');
    const top = x.for[0];
    it.append(nameEl(x, tipsHere), el('span', 'why', top ? SHORT[top.key] ?? top.key : ''));
    const res = resultEl(x);
    if (res) it.append(res);
    cases.append(it);
  });
  if (also.length > 3) cases.append(el('div', 'dim', `+${also.length - 3} more`));
  r.append(cases);

  const book = cell('c-book', el('div', 'lbl', 'YOUR BOOK'));
  const booked = race.runners.filter((x) => x.blackbook?.live);
  if (!booked.length) { book.classList.add('none'); book.append(el('span', 'dim', DASH)); }
  booked.forEach((x) => {
    const it = el('div', 'pick');
    it.append(el('span', 'nm booked', `${x.horse_no} ${x.horse_name}`),
              el('span', `setup ${x.setup.toLowerCase()}`, `${x.setup} ${times(x.setup_x)}`),
              el('span', 'dim', ` · ${ordinal(x.rank)}`));
    it.title = [x.blackbook.reasoning ?? '', '', setupText(x)].join(NL);
    book.append(it);
  });
  r.append(book);
  return r;
}

/** What made a set-up verdict, one line per piece. */
function setupText(x) {
  return (x.setup_parts ?? []).map((p) => `${times(p.x)}  ${p.label}`).join(NL)
    || 'nothing measured moves it either way';
}

/* ── one race, open ─────────────────────────────────────────────────────── */

function chip(f, side) {
  const c = el('span', `scr-x ${side}`, `${SHORT[f.key] ?? f.key} ${times(f.x)}`);
  c.title = `${f.label}${f.why ? ` — ${f.why}` : ''}${f.caveat ? `\n(${f.caveat})` : ''}`;
  return c;
}

function bar(p) {
  const b = el('span', 'scr-bar');
  const i = el('i');
  i.style.width = `${Math.max(2, Math.min(100, p))}%`;
  b.append(i);
  return b;
}

function context(x) {
  const box = el('div', 'ctx');
  const line = (label, cls, ...kids) => {
    const l = el('div', `ln ${cls ?? ''}`);
    l.append(el('span', 'lb', label), ...kids);
    box.append(l);
  };
  const b = x.blackbook;
  if (b) {
    const t = el('span', null,
      `${b.live ? '' : 'CLOSED · '}${b.confidence ?? ''} · ${b.reasoning ?? ''}`);
    const cond = b.conditions_text
      ? el('span', b.on_conditions ? 'met' : 'unmet',
           ` · ${b.conditions_text} ${b.on_conditions ? '— met today' : '— NOT met today'}`)
      : el('span', 'dim', ' · no conditions written');
    const set = el('span', `setup ${x.setup.toLowerCase()}`, ` · SET-UP ${x.setup} ${times(x.setup_x)}`);
    set.title = `today's circumstances, apart from the horse's form:\n${setupText(x)}`;
    line('BOOK', 'book', t, cond, set);
  }
  x.notes.forEach((n) => line('NOTE', 'note', el('span', null,
    `${compactDate(n.race_date)} R${n.race_no} · ${n.note}`)));
  const ls = x.last_start;
  if (ls) {
    const words = ls.incident_comment && !/^no report/i.test(ls.incident_comment)
      ? ls.incident_comment : ls.running_comment;
    line('LAST', null,
      el('span', 'k', `${compactDate(ls.race_date)} ${ordinal(ls.place)}/${ls.field_size} `
        + `${ls.venue ?? ''} ${ls.distance ?? ''}m ${classLabel(ls.race_class) ?? ''} g${drawText(ls.draw)} `
        + `${ls.jockey ?? ''}`),
      el('span', null, words ? ` · ${words}` : ' · no comment published yet'));
  }
  const t = x.trial;
  if (t) {
    line('TRIAL', null,
      el('span', `k band-${(t.band ?? '').toLowerCase()}`,
         `${compactDate(t.trial_date)} ${t.band ?? ''} ${t.place ? `${t.place}/${t.field_size}` : ''}`
         + `${t.jockey ? ` ${t.jockey}` : ''}`),
      el('span', null, t.comment ? ` · ${t.comment}` : ''),
      el('span', 'note', t.note ? ` · NOTE ${t.note}` : ''));
  }
  x.reversals.forEach((v) => line('H2H', null,
    el('span', null, v.note),
    el('span', v.moved.length ? 'for' : 'dim',
       v.moved.length ? ` · now: ${v.moved.join(', ')}` : ' · nothing measured has moved')));
  return box;
}

function runnerRow(x, tipsHere, run) {
  const r = el('div', `scr-runner t-${x.tier.toLowerCase()}`);
  const line = el('div', 'main');
  line.append(el('span', 'rk', String(x.rank)), nameEl(x, tipsHere));
  const who = el('span', 'who', `${x.jockey ?? DASH} · g${drawText(x.draw)}`);
  who.title = `${x.rider.why ?? ''}\nrider against this field: ${times(x.rider.x_vs_field)}`;
  line.append(who, styleBadge(x.style));
  const chance = el('span', 'chance');
  chance.append(bar(x.place_pct), el('b', null, pct(x.place_pct)),
                el('span', 'dim', ` win ${pct(x.win_pct)}`));
  chance.title = 'chance to place and to win in this field, before any price';
  const form = el('span', 'form', x.form_rank ? `form ${x.form_rank}` : 'unrated');
  form.title = 'rank on the SARR form rating alone, for comparison';
  line.append(chance, form);
  const chips = el('span', 'chips');
  x.for.forEach((f) => chips.append(chip(f, 'for')));
  x.against.forEach((f) => chips.append(chip(f, 'against')));
  line.append(chips);
  if (run) line.append(resultEl(x) ?? el('span', 'scr-res', DASH));
  r.append(line, context(x));
  return r;
}

function detail(race, tipsHere) {
  const d = el('div', 'scr-detail');
  const cap = el('div', 'cap');
  // The all-weather's course and surface are both "AWT"; say it once.
  const track = [...new Set([race.surface, race.course].filter(Boolean))].join(' ');
  cap.append(el('span', null, `R${race.race_no} · ${race.distance}m ${track} · `
    + `${classLabel(race.race_class) ?? DASH} · ${race.field_size} runners`));
  const link = el('a', 'go', 'RACE DAY ▸');
  link.href = `raceday.html?race=${race.race_no}`;
  link.addEventListener('click', (e) => e.stopPropagation());
  cap.append(link);
  d.append(cap);
  race.runners.forEach((x) => d.append(runnerRow(x, tipsHere, race.run)));
  return d;
}

/* ── what it weighs, and what it is not ─────────────────────────────────── */

function foot(scr) {
  const f = el('div', 'scr-foot');
  const ae = scr.fit?.above_tote_ae;
  f.append(el('div', null,
    'A chip is a multiplier on the horse\'s chance on top of its SARR form rating, fitted on '
    + `${scr.fit?.runs?.toLocaleString() ?? ''} runs (${scr.fit?.seasons ?? ''}) and tested season `
    + 'by season on races it had not seen. Weight swings are left out on purpose: in a handicap '
    + 'the weight follows the rating, so the horse a swing goes against is usually the improver.'));
  if (ae) {
    f.append(el('div', 'warn',
      `NOT A VALUE SIGNAL. Where the Screen rates a horse 1.25–2× the closing tote's chance, `
      + `the tote has been right (A/E ${ae['x1.25-2'].toFixed(2)}; ${ae['x2+'].toFixed(2)} beyond `
      + `2×, ${ae.runs.toLocaleString()} runners). It finds the horses to read; the price decides.`));
  }
  const t = el('button', 'scr-toggle', state.weights ? 'HIDE WHAT IT WEIGHS' : 'WHAT IT WEIGHS ▸');
  t.addEventListener('click', () => { state.weights = !state.weights; draw(); });
  f.append(t);
  if (state.weights) {
    const g = el('div', 'scr-weights');
    scr.factors.forEach((x) => {
      const it = el('div', 'w');
      it.append(el('span', `x ${x.x >= 1 ? 'for' : 'against'}`, times(x.x)),
                el('span', null, x.label),
                el('span', 'dim', x.runs ? ` · ${x.runs.toLocaleString()} runs` : ' · per step'),
                el('span', 'dim', x.caveat ? ` · ${x.caveat}` : ''));
      g.append(it);
    });
    f.append(g);
  }
  return f;
}
