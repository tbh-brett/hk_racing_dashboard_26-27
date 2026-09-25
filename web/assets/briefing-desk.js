/* briefing-desk.js — the Briefing's desktop list, and the pieces the phone
 * list shares with it.
 *
 * Off web/design-source/Briefing.dc.html: "worth studying first" races at
 * full height — their reasons marked SCREEN, SAID or PRICE, the pace, the
 * Screen's four with who backs them and their price — then the rest in race
 * order, one line each, and races already run collapsed to their winner. An
 * open race shows its interviews first, then every runner, each opening to
 * the Screen's reasons, what was said about it verbatim, and its prices.
 */
import { el, classCell, styleClass } from './vocab.js';

/* ── shared pieces ──────────────────────────────────────────────────────── */

/** Quotes, verbatim. A long one folds to a line and opens on click; each
 *  links to the second it was said, or to the page it was written on. */
export function wordRows(list, { size = 'm', voice = false, quoted = true, caret = true } = {}) {
  const out = [];
  list.forEach((w) => {
    const row = el('div', `bf-w s-${size}${w.long ? ' long' : ''}`);
    const car = el('span', 'caret', w.long ? '▸' : '');
    const q = el('span', 'q', quoted ? `“${w.text}”` : w.text);
    const flip = (e) => {
      if (!w.long) return;
      e.stopPropagation();
      car.textContent = row.classList.toggle('open') ? '▾' : '▸';
    };
    car.addEventListener('click', flip);
    q.addEventListener('click', flip);
    if (caret) row.append(car);
    row.append(q);
    if (w.url) {
      const a = el('a', `t${voice ? ' voice' : ''}`, `▸ ${w.tl}`);
      a.href = w.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.addEventListener('click', (e) => e.stopPropagation());
      row.append(a);
    }
    out.push(row);
    if (w.en) out.push(el('div', 'bf-en', w.en));
  });
  return out;
}

export function markEl(m) {
  const s = el('span', `bf-mark k-${m.tone}`, m.t);
  if (m.heard) {
    const h = el('span', 'heard', '~');
    h.title = 'heard: the number was said aloud and the name came through speech-to-text';
    s.append(h);
  }
  return s;
}

export const chipEl = (c) => el('span', `bf-chip t-${c.tone}`, c.t);

export function kindEl(x) {
  return el('span', `bf-kind k-${x.tone}`, x.tag);
}

export function paceBar(segs, cls = '') {
  const bar = el('div', `bf-pacebar ${cls}`);
  segs.forEach((s) => {
    const seg = el('div', styleClass(s.style, { chip: false }));
    seg.style.flex = String(s.n);
    bar.append(seg);
  });
  return bar;
}

export function styleEl(x) {
  return el('span', styleClass(x.style), x.styleShort);
}

export function link(href, text, cls) {
  const a = el('a', cls, text);
  a.href = href;
  a.addEventListener('click', (e) => e.stopPropagation());
  return a;
}

export function nameEl(text, booked, cls = 'nm') {
  return el('span', `${cls}${booked ? ' booked' : ''}`, text);
}

export function bar(pct, cls = '') {
  const b = el('span', `bf-meter ${cls}`);
  const fill = el('span', 'fill');
  fill.style.width = `${pct}%`;
  b.append(fill);
  return b;
}

/* ── the desktop list ───────────────────────────────────────────────────── */

export function renderDesk(host, view, ctx) {
  host.replaceChildren();
  view.list.forEach((r) => {
    if (r.head) {
      const h = el('div', 'bf-sec');
      h.append(el('span', 'l', r.l), el('span', 'r', r.r));
      host.append(h);
      return;
    }
    const wrap = el('div', 'bf-race');
    wrap.dataset.race = String(r.no);
    if (r.mode === 'run') wrap.append(runRow(r, ctx));
    else wrap.append(r.mode === 'feat' ? featRow(r, ctx) : quietRow(r, ctx));
    if (r.open) wrap.append(raceDetail(r, ctx));
    host.append(wrap);
  });
}

function timeCell(r, big) {
  const t = el('div', 'when');
  t.append(el('span', big ? 'lbl big' : 'lbl', r.label), el('span', 'off', r.off),
           el('span', `mins${r.minsStrong ? ' strong' : ''}`, r.mins));
  return t;
}

function runRow(r, ctx) {
  const row = el('div', 'bf-ranrow');
  row.append(el('span', 'lbl', r.label), el('span', 'off', r.off),
             el('span', 'ran', 'RAN'), el('span', 'win', r.winner),
             link(`results.html?date=${ctx.date}&race=${r.no}`, 'RESULT ▸', 'res'));
  return row;
}

function featRow(r, ctx) {
  const row = el('div', `bf-feat e-${r.edge || 'none'}${r.open ? ' open' : ''}`);
  row.addEventListener('click', () => ctx.act.race(r.no));

  const a = el('div', 'c-id');
  a.append(timeCell(r, true));
  const meta = el('div', 'meta');
  meta.append(el('span', null, r.dist));
  const cls = classCell(r.raceClass, { restricted: r.restricted });
  if (cls) meta.append(cls);
  meta.append(el('span', null, r.course), el('span', null, r.field));
  a.append(meta);
  if (r.market) {
    const mk = el('div', 'mkt');
    const fav = el('div');
    fav.append(document.createTextNode('FAV '), el('b', null, `#${r.market.favNo}`),
               document.createTextNode(' '), el('b', 'big', r.market.favWin),
               document.createTextNode(' '), el('span', 'dim', r.market.favTag));
    const pool = el('div');
    pool.append(document.createTextNode(`${r.market.conc} · POOL `), el('span', 'v', r.market.pool));
    mk.append(fav, pool);
    a.append(mk);
  }

  const b = el('div', 'c-why');
  r.reasons.slice(0, 4).forEach((x) => {
    const line = el('div', 'reason');
    line.append(kindEl(x), el('span', 'txt', x.text));
    b.append(line);
  });
  if (!r.reasons.length) b.append(el('div', 'more', 'nothing to read yet'));
  if (r.reasons.length > 4) {
    b.append(el('div', 'more', `+${r.reasons.length - 4} more when open · ${r.kindCounts}`));
  }

  const c = el('div', 'c-pace');
  c.append(el('div', 'cap', 'PACE'), el('div', 'head', r.pace.head), paceBar(r.pace.segs));
  const key = el('div', 'key');
  r.pace.segs.forEach((s) => key.append(el('span', styleClass(s.style, { chip: false }), s.t)));
  c.append(key);

  const d = el('div', 'c-four');
  const hd = el('div', 'bf-four hd');
  hd.append(el('span'), el('span', null, "SCREEN'S FOUR"), el('span', null, 'PLACE'), el('span'),
            el('span', null, 'SAID'), el('span', 'r', r.hasPrices ? 'TOTE' : ''));
  d.append(hd);
  r.shortlist.forEach((x) => {
    const line = el('div', 'bf-four');
    const said = el('span', 'said');
    said.append(el('span', x.tip ? 'tip' : 'dim', x.said));
    if (x.int) said.append(el('span', 'voice', ' INT'));
    const price = el('span', 'r');
    price.append(el('span', 'dim', `${x.mrank} `), el('span', null, x.price));
    line.append(el('span', 'rk', String(x.rank)), nameEl(`${x.no} ${x.name}`, x.booked),
                bar(x.bar), el('span', 'pl', x.place), said, price);
    d.append(line);
  });
  row.append(a, b, c, d);
  return row;
}

function quietRow(r, ctx) {
  const row = el('div', `bf-quiet e-${r.edge || 'none'}${r.open ? ' open' : ''}`);
  row.addEventListener('click', () => ctx.act.race(r.no));
  const a = el('div', 'c-id');
  a.append(el('span', 'lbl', r.label), el('span', 'off', r.off),
           el('span', `mins${r.minsStrong ? ' strong' : ''}`, r.mins));
  const cls = classCell(r.raceClass, { restricted: r.restricted });
  if (cls) a.append(cls);
  a.append(el('span', 'dist', r.dist));
  const b = el('div', 'c-why');
  b.append(el('span', 'counts', r.kindCounts), el('span', 'first', r.firstReason));
  const c = el('div', 'c-pace');
  c.append(paceBar(r.pace.segs, 'thin'), el('div', 'short', r.pace.head));
  const d = el('div', 'c-four');
  d.append(document.createTextNode("SCREEN'S FOUR · PLACE "), el('span', 'v', r.slCompact));
  row.append(a, b, c, d);
  return row;
}

/* ── an opened race ─────────────────────────────────────────────────────── */

function raceDetail(r, ctx) {
  const box = el('div', 'bf-open');
  const top = el('div', 'bf-openhead');
  const cls = classCell(r.raceClass, { restricted: r.restricted });
  top.append(el('span', null, `${r.dist} ·`));
  if (cls) top.append(cls);
  top.append(el('span', null, `· ${r.course} · GOING ${r.going} · ${r.field}`));
  r.poolBits.forEach((p) => {
    const s = el('span', null, `${p.l} `);
    s.append(el('span', 'v', p.v));
    top.append(s);
  });
  top.append(link(`raceday.html?date=${ctx.date}&race=${r.no}`, 'RACE DAY CARD ▸', 'card'));
  box.append(top);

  r.interviews.forEach((iv) => box.append(interviewBox(iv)));
  r.comments.forEach((cm) => {
    const line = el('div', 'bf-comment');
    line.append(el('span', 'who', `${cm.who} · RACE`), ...wordRows([cm.w], { quoted: false }));
    box.append(line);
  });

  const grid = runGrid(r);
  const head = el('div', 'bf-runhead');
  head.style.setProperty('--bf-run-grid', grid);
  ['SCR', 'NO', 'HORSE · DRAW · JOCKEY · TRAINER', 'STYLE', 'SCREEN · PLACE / WIN', '',
   'BLACKBOOK', 'SAID · SOURCES'].forEach((t) => head.append(el('span', null, t)));
  if (r.hasPrices) {
    const ph = el('span', 'prices');
    ph.style.setProperty('--bf-price-cols', r.books.map(() => '44px').join(' '));
    r.priceHead.forEach((h) => {
      const c = el('span', null, h.s);
      c.append(el('span', `sub${h.stale ? ' stale' : ''}`, h.sub));
      ph.append(c);
    });
    head.append(ph, el('span', 'r', 'MOVE'));
  }
  head.append(el('span'));
  box.append(head);

  r.runners.forEach((x) => {
    const open = r.openRunner === x.no;
    const row = el('div', `bf-runner${open ? ' open' : ''}${x.unplaced ? ' out' : ''}`);
    row.style.setProperty('--bf-run-grid', grid);
    row.addEventListener('click', () => ctx.act.runner(r.no, open ? null : x.no));
    const who = el('span', 'who');
    const n1 = el('span', 'n1');
    n1.append(nameEl(x.name, x.booked), el('span', 'zh', x.zh));
    who.append(n1, el('span', 'n2', x.line2));
    const sc = el('span', 'sc');
    sc.append(bar(x.bar, x.top ? 'top' : ''), el('span', 'pl', x.place), el('span', 'wn', x.win));
    const bb = el('span', 'bb');
    if (x.hasBB) {
      bb.append(el('span', 'bf-bbtag', 'BB'), el('span', x.setupAgainst ? 'dim' : 'v', ` ${x.setup}`));
    }
    const said = el('span', 'said');
    said.append(el('span', `n ${x.tip ? 'tip' : 'dim'}`, x.said));
    x.marks.forEach((m) => said.append(markEl(m)));
    const style = el('span');
    style.append(styleEl(x));
    row.append(el('span', 'rk', String(x.rk)), el('span', 'no', String(x.no)), who,
               style, sc, el('span', `tier${x.top ? ' top' : ''}`, x.tier), bb, said);
    if (r.hasPrices) {
      const pc = el('span', 'prices');
      pc.style.setProperty('--bf-price-cols', r.books.map(() => '44px').join(' '));
      x.cells.forEach((c) => pc.append(el('span', `${c.on ? '' : 'off'}${c.most ? ' most' : ''}`, c.v)));
      row.append(pc, el('span', `move${x.move ? ` t-${x.move.tone}` : ''}`, x.move ? x.move.t : ''));
    }
    const tail = el('span', 'tail');
    x.chips.forEach((c) => tail.append(chipEl(c)));
    tail.append(el('span', 'caret', open ? '▾' : '▸'));
    row.append(tail);
    box.append(row);
    if (open) box.append(runnerDetail(x));
  });
  return box;
}

function runGrid(r) {
  const prices = r.hasPrices
    ? ` ${r.books.length * 44 + (r.books.length - 1) * 6}px 58px` : '';
  return `24px 22px minmax(0,1.3fr) 30px 150px 58px 116px minmax(0,1.2fr)${prices} 64px`;
}

export function interviewBox(iv) {
  const box = el('div', 'bf-iv');
  const h = el('div', 'hd');
  h.append(el('span', 'role', iv.role), el('span', 'spk', iv.speaker), el('span', 'on', 'on'),
           nameEl(iv.horse, iv.booked, 'horse'), el('span', 'sc', iv.screen),
           el('span', 'src', 'RACING TO WIN INTERVIEW'));
  box.append(h, ...wordRows(iv.words, { size: 'l', voice: true }));
  return box;
}

/* ── one runner, opened ─────────────────────────────────────────────────── */

export function screenBlock(x, { compact = false } = {}) {
  const col = el('div', 'bf-col');
  if (!compact) col.append(el('div', 'cap', `THE SCREEN · ${x.setupLine}`));
  const reasons = el('div', 'bf-factors');
  x.forList.forEach((f) => {
    const c = el('span', 'for', `${f.why} `);
    c.title = f.title;
    c.append(el('span', 'x', f.x));
    reasons.append(c);
  });
  x.againstList.forEach((f) => {
    const c = el('span', 'against', `${f.why} `);
    c.title = f.title;
    c.append(el('span', 'x', f.x));
    reasons.append(c);
  });
  col.append(reasons);
  if (x.hasBB && !compact) {
    const bb = el('div', 'bf-bookbox');
    bb.append(el('div', 'hd', `YOUR BOOK · ${x.bbHead}`), el('div', 'why', x.bbWhy));
    col.append(bb);
  }
  if (x.last) {
    const ls = el('div', 'bf-last');
    const hd = el('div', 'hd');
    hd.append(el('span', 'cap', 'LAST START'), el('span', 'v', x.last.head));
    const cls = classCell(x.last.raceClass);
    if (cls) hd.append(cls);
    if (!compact) x.last.tags.forEach((t) => hd.append(el('span', 'bf-tag', t)));
    ls.append(hd);
    if (x.last.run) ls.append(el('div', 'run', x.last.run));
    if (x.last.inc && !compact) {
      const inc = el('div', 'inc');
      inc.append(el('span', 'lab', 'STEWARDS'), ...wordRows([x.last.inc], { quoted: false }));
      ls.append(inc);
    }
    col.append(ls);
  }
  if (x.trial) {
    const t = el('div', 'bf-trial');
    t.append(el('span', 'cap', 'TRIAL '), el('span', x.trial.good ? 'good' : 'dim', x.trial.head),
             document.createTextNode(` ${x.trial.comment}`));
    col.append(t);
  }
  x.notes.forEach((n) => {
    const t = el('div', 'bf-note');
    t.append(el('span', 'cap', 'YOUR NOTE '), document.createTextNode(n));
    col.append(t);
  });
  if (x.reversals.length && !compact) {
    col.append(el('div', 'cap gap', 'COULD TURN AROUND'));
    x.reversals.forEach((v) => {
      const t = el('div', 'bf-rev');
      t.append(el('b', null, `#${v.no} ${v.name}`),
               el('span', 'dim', ` ${v.note}${v.moved ? ` · ${v.moved}` : ''}`));
      col.append(t);
    });
  }
  return col;
}

export function saidBlock(x, { compact = false } = {}) {
  const col = el('div', 'bf-col');
  if (!compact) col.append(el('div', 'cap', `WHAT WAS SAID · ${x.saidHead}`));
  x.srcs.forEach((b) => {
    const s = el('div', 'bf-said');
    const hd = el('div', 'hd');
    hd.append(markEl(b.m), el('span', 'who', b.who));
    if (!b.words.length && !compact) {
      hd.append(el('span', 'none', b.voice ? 'quoted in the interview above' : b.noWordsMsg));
      if (b.url) {
        const a = link(b.url, `▸ ${b.tl}`, 'go');
        a.target = '_blank';
        hd.append(a);
      }
    }
    s.append(hd, ...(b.voice && !compact ? [] : wordRows(b.words, { voice: b.voice })));
    col.append(s);
  });
  if (!x.srcs.length && !compact) col.append(el('div', 'bf-nosaid', x.noSaidMsg));
  if (x.form) {
    const f = el('div', 'bf-form');
    f.append(el('span', 'lab', 'R&S FORM'));
    const t = el('span', 'txt', `${x.form.text} `);
    t.append(el('span', 'dim', 'form, not counted'));
    f.append(t);
    if (x.form.url && !compact) {
      const a = link(x.form.url, '▸ page', 'go');
      a.target = '_blank';
      f.append(a);
    }
    col.append(f);
  }
  return col;
}

export function priceBlock(x, { compact = false } = {}) {
  const col = el('div', 'bf-col');
  if (!compact) col.append(el('div', 'cap', `PRICES · ${x.priceHead}`));
  const t = el('div', `bf-pt${compact ? ' m' : ''}`);
  t.style.setProperty('--bf-pt-n', String(x.pt.head.length));
  t.append(el('span', 'h'));
  x.pt.head.forEach((h) => t.append(el('span', 'h r', h)));
  x.pt.rows.forEach((row) => {
    t.append(el('span', 'lab', compact ? row.short : row.long));
    row.cells.forEach((c) => t.append(el('span', `c ${c.tone}`, c.v)));
  });
  col.append(t);
  if (!compact) col.append(el('div', 'bf-ptnote', x.ptNote));
  return col;
}

function runnerDetail(x) {
  const box = el('div', `bf-rdetail${x.pt ? ' priced' : ''}`);
  box.append(screenBlock(x), saidBlock(x));
  if (x.pt) box.append(priceBlock(x));
  return box;
}

/* ── below the list ─────────────────────────────────────────────────────── */

export function renderPanels(host, view, ctx) {
  host.replaceChildren();
  const left = el('div', 'bf-panel');
  left.append(el('div', 'cap', 'SOURCES · WHAT IS IN, WHAT IS DUE'));
  view.clock.forEach((c) => {
    const row = el('div', 'bf-srcrow');
    const lab = el('span', 'label', `${c.label} `);
    if (c.counted) lab.append(el('span', 'dim', c.counted));
    row.append(el('span', `ab a-${c.abTone}`, c.ab), lab,
               el('span', `st t-${c.tone}`, `${c.mark} ${c.txt}`), el('span', 'detail', c.detail));
    left.append(row);
  });
  const right = el('div', 'bf-panel');
  const hd = el('div', 'cap');
  hd.append(document.createTextNode('WHERE THE MARKETS DISAGREE '),
            el('span', 'dim', 'price × the other market’s fair chance − 1 · +5% or more'));
  right.append(hd);
  view.edges.forEach((e) => {
    const row = el('div', `bf-edge${e.ran ? ' ran' : ''}`);
    row.addEventListener('click', () => ctx.act.edge(e.race, e.no));
    const at = el('span', 'r');
    at.append(el('span', 'dim', `${e.at} `), document.createTextNode(e.price));
    const fair = el('span', 'r small');
    fair.append(document.createTextNode(`${e.against} `), el('span', 'v', e.fair));
    row.append(el('span', 'race', `R${e.race}`), nameEl(`${e.no} ${e.name}`, e.booked), at, fair,
               el('span', 'r ev', e.ev), el('span', `r small ${e.tip ? 'tip' : 'dim'}`, e.src));
    right.append(row);
  });
  if (!view.edges.length) right.append(el('div', 'bf-nosaid', view.noEdgesMsg));
  host.append(left, right);
}
