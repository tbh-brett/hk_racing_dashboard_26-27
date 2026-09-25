/* briefing-phone.js — the Briefing at 390px, at the course.
 *
 * The same list as the desktop, in the same order, stacked: a race card
 * carries its time to the off, the favourite, its lead reasons, the pace and
 * the Screen's four; opened, its interviews come first, then every runner in
 * the Screen's order with how many sources back it and its tote price, each
 * opening to its reasons, what was said and every market's price.
 */
import { el, classCell } from './vocab.js';
import {
  wordRows, markEl, chipEl, kindEl, paceBar, styleEl, link, nameEl, bar,
  interviewBox, screenBlock, saidBlock, priceBlock,
} from './briefing-desk.js';

export function renderPhone(host, view, ctx) {
  host.replaceChildren();
  view.list.forEach((r) => {
    if (r.head) {
      const h = el('div', 'bf-psec');
      h.append(el('div', 'l', r.l), el('div', 'r', r.rs));
      host.append(h);
      return;
    }
    const wrap = el('div', 'bf-prace');
    wrap.dataset.race = String(r.no);
    if (r.mode === 'run') {
      const row = el('div', 'bf-pran');
      row.append(el('span', 'lbl', r.label), el('span', null, r.off), el('span', 'win', r.winner),
                 link(`results.html?date=${ctx.date}&race=${r.no}`, 'RESULT ▸', 'res'));
      wrap.append(row);
    } else {
      wrap.append(card(r, ctx));
      if (r.mOpen) wrap.append(opened(r, ctx));
    }
    host.append(wrap);
  });
  host.append(el('div', 'bf-pfoot', view.fitLine));
}

function card(r, ctx) {
  const c = el('div', `bf-pcard e-${r.mEdge || 'none'}${r.mOpen ? ' open' : ''}`);
  c.addEventListener('click', () => ctx.act.raceM(r.no));
  const hd = el('div', 'hd');
  hd.append(el('span', 'lbl', r.label), el('span', 'off', r.off),
            el('span', `mins${r.minsStrong ? ' strong' : ''}`, r.mins));
  const cls = classCell(r.raceClass, { restricted: r.restricted });
  if (cls) hd.append(cls);
  hd.append(el('span', 'dist', r.dist),
            el('span', 'fav', r.market ? `FAV #${r.market.favNo} ${r.market.favWin}` : ''));
  c.append(hd);
  if (r.mode === 'feat') {
    const why = el('div', 'why');
    r.reasons.slice(0, 3).forEach((x) => {
      const line = el('div', 'reason');
      line.append(kindEl(x), el('span', 'txt', x.text));
      why.append(line);
    });
    if (r.reasons.length > 3) why.append(el('div', 'more', `+${r.reasons.length - 3} more · ${r.kindCounts}`));
    c.append(why, paceBar(r.pace.segs, 'thin'), el('div', 'pace', r.pace.head));
    const four = el('div', 'four');
    r.shortlist.forEach((x) => {
      const line = el('div', 'bf-pfour');
      const said = el('span', 'said');
      said.append(el('span', x.tip ? 'tip' : 'dim', x.saidS));
      if (x.int) said.append(el('span', 'voice', ' INT'));
      line.append(nameEl(`${x.no} ${x.name}`, x.booked), bar(x.bar), el('span', 'pl', x.place),
                  said, el('span', 'px', x.price));
      four.append(line);
    });
    c.append(four);
  } else {
    const q = el('div', 'quiet');
    q.append(el('span', null, r.kindCounts), el('span', 'sl', r.slCompact));
    c.append(q);
  }
  return c;
}

function opened(r, ctx) {
  const box = el('div', 'bf-popen');
  r.interviews.forEach((iv) => box.append(interviewBox(iv)));
  if (r.poolBits.length) {
    const pools = el('div', 'pools');
    r.poolBits.forEach((b) => {
      const s = el('span', null, `${b.l} `);
      s.append(el('span', 'v', b.v));
      pools.append(s);
    });
    box.append(pools);
  }
  r.comments.forEach((cm) => {
    const line = el('div', 'bf-comment');
    line.append(el('span', 'who', `${cm.who} · RACE`), ...wordRows([cm.w], { quoted: false }));
    box.append(line);
  });
  const hd = el('div', 'bf-prunhead');
  hd.append(el('span', 'grow', `ALL ${r.fieldN} · SCREEN ORDER`), el('span', null, 'PLACE'),
            el('span', 'w40', 'SAID'), el('span', 'w44', r.hasPrices ? 'TOTE' : ''));
  box.append(hd);
  r.runners.forEach((x) => {
    const open = r.openRunnerM === x.no;
    const row = el('div', `bf-prunner${open ? ' open' : ''}${x.unplaced ? ' out' : ''}`);
    row.addEventListener('click', () => ctx.act.runnerM(r.no, open ? null : x.no));
    const l1 = el('div', 'l1');
    l1.append(el('span', 'rk', String(x.rk)), el('span', 'no', String(x.no)),
              nameEl(x.name, x.booked, 'nm grow'), el('span', 'pl', x.place),
              el('span', `w40 ${x.tip ? 'tip' : 'dim'}`, x.saidS),
              el('span', `w44 px${x.mPriceMost ? ' most' : ''}`, x.mPrice));
    const l2 = el('div', 'l2');
    l2.append(styleEl(x), el('span', `tier${x.top ? ' top' : ''}`, x.tier));
    if (x.hasBB) l2.append(el('span', 'bf-bbtag', `BB ${x.setup}`));
    x.marks.forEach((m) => l2.append(markEl(m)));
    x.chips.forEach((ch) => l2.append(chipEl(ch)));
    if (x.move) l2.append(el('span', `move t-${x.move.tone}`, x.move.t));
    row.append(l1, l2);
    box.append(row);
    if (open) {
      const d = el('div', 'bf-prdetail');
      d.append(el('div', 'line2', x.line2), screenBlock(x, { compact: true }),
               saidBlock(x, { compact: true }));
      if (x.pt) d.append(priceBlock(x, { compact: true }));
      box.append(d);
    }
  });
  return box;
}
