/* briefing-talk.js — what the pundits' videos discussed, section by section.
 *
 * Brett, 30 Sep 2026: "whatever is discussed is summarised, identified and
 * included on the dashboard". From the Briefing answer's `talk`
 * (query/tips_talk): per video, each section's first line as its heading —
 * 「講開巴度停賽期滿復出」, 「伍鵬志被喻為季初最大發現」 — the whole of it a
 * click away, verbatim, and every runner it names as a link to that runner's
 * race, with the jockey and trainer beside it, which says who 巴度 or 伍鵬志
 * is. The tipster's picks from the video's tail close it. What is said about
 * one horse is also under that horse, as before; this is the rest.
 */
import { el } from './vocab.js';

const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const opened = new Set();          // sections the reader opened, kept across re-reads
let shown = null;                  // the whole block, as the reader last left it

function timeLink(url, t) {
  const a = el('a', 'at', `▸ ${t === null || t === undefined ? 'video' : clock(t)}`);
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener';
  a.addEventListener('click', (e) => e.stopPropagation());
  return a;
}

function runnerChip(x, ctx, extra = '') {
  const b = el('button', 'rn');
  b.type = 'button';
  b.title = `Race ${x.race_no}, #${x.horse_no} ${x.horse_name} ${x.name_zh || ''} — open it`;
  b.append(el('span', 'id', `R${x.race_no} #${x.horse_no}`), el('span', 'nm', x.horse_name),
           el('span', 'who', [x.jockey, x.trainer].filter(Boolean).join(' · ') + extra));
  b.addEventListener('click', (e) => { e.stopPropagation(); ctx.act.edge(x.race_no, x.horse_no); });
  return b;
}

/** `stage` is the Briefing's: before race day the block is open, because
 *  that is when it is read; from race day on it folds to its title line, so
 *  the races come first, and opens on a click. */
export function renderTalk(host, talk, ctx, stage) {
  host.replaceChildren();
  const open = shown ?? !['race_day', 'settled'].includes(stage);
  host.classList.toggle('folded', !open);
  (talk || []).forEach((v) => {
    const cap = el('div', 'bf-talk-cap');
    cap.title = open ? 'click to fold' : 'click to read what was discussed';
    cap.addEventListener('click', () => { shown = !open; renderTalk(host, talk, ctx, stage); });
    cap.append(el('span', 'fold', open ? '▾' : '▸'),
               el('span', 'l', `${v.label} · WHAT WAS DISCUSSED`),
               el('span', 'r', `${v.sections.length} sections${v.picks.length
                 ? ` · ${v.picks.length} picks` : ''} · each horse opens its race`),
               timeLink(v.url, null));
    host.append(cap);
    if (!open) return;

    v.sections.forEach((s) => {
      const key = `${v.video_id}:${s.t}`;
      const row = el('div', `bf-talk-row${opened.has(key) ? ' open' : ''}`);
      row.title = 'click to read the whole section';
      row.addEventListener('click', () => {
        if (opened.has(key)) opened.delete(key); else opened.add(key);
        row.classList.toggle('open');
      });
      const words = el('div', 'words');
      // The heading is the section's first clause; the text carries on from it.
      const rest = s.text.slice(s.head.length).replace(/^[，。]/, '');
      words.append(el('div', 'head', s.head));
      if (rest) words.append(el('div', 'text', rest));
      const who = el('div', 'runners');
      s.runners.forEach((x) => who.append(runnerChip(x, ctx)));
      if (s.unlinked.length) who.append(el('span', 'also', `also named: ${s.unlinked.join('、')}`));
      if (!s.runners.length && !s.unlinked.length) who.append(el('span', 'also', 'no runner named'));
      row.append(timeLink(s.url, s.t), words, who);
      host.append(row);
    });

    if (v.picks.length) {
      const row = el('div', 'bf-talk-row picks');
      const tipsters = [...new Set(v.picks.map((p) => p.tipster))].join(' · ');
      const words = el('div', 'words');
      words.append(el('div', 'head', `${tipsters}'s picks`),
                   el('div', 'note', v.picks.some((p) => p.heard)
                     ? '~ heard through speech-to-text: the number is the pick, the name a check' : ''));
      const who = el('div', 'runners');
      v.picks.forEach((p) => who.append(runnerChip(p, ctx, p.rank === 1 ? ' · TOP PICK' : '')));
      row.append(timeLink(v.picks[0].url, v.picks[0].t), words, who);
      host.append(row);
    }
  });
}
