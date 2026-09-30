/* briefing-format.js — how the Briefing writes a time, a price, a quote and
 * a source's mark. Pure functions and the tables they read; no DOM.
 *
 * Split out of briefing-model.js on 28 Sep 2026, when the model reached its
 * 500 lines: the model reads the answer, this writes it down.
 */
import { DASH, MINUS } from './vocab.js';

export const AB = {
  factcheck: 'FC', rtw_preview: 'RTW', rtw_interview: 'INT', tote: 'TOTE',
  racing_sports: 'R&S', ladbrokes: 'LB', threads: 'HD', bryan: 'BRY',
};
/* The voices counted as support. Anything else a source said is shown with
 * "shown, not counted" and a dotted mark. */
export const COUNTED = new Set(['factcheck', 'rtw_preview', 'rtw_interview',
                                'racing_sports', 'threads']);
export const FOLD_AT = 110;
export const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
                    'Oct', 'Nov', 'Dec'];

/* ── formatting ─────────────────────────────────────────────────────────── */

export const pd = (s) => new Date(s.length === 10 ? `${s}T00:00` : s);
export const hm = (s) => (s ? s.slice(11, 16) : '');
export const dayDiff = (a, b) => Math.round((pd(a.slice(0, 10)) - pd(b.slice(0, 10))) / 864e5);
export const minutesBetween = (a, b) => Math.round((pd(a) - pd(b)) / 6e4);

/** "tonight 20:00", "tomorrow 16:00", "Sat 09:00" — against the as-of time. */
export function when(iso, asOf) {
  const dd = dayDiff(iso, asOf);
  const t = hm(iso);
  if (dd === 0) return `${Number(t.slice(0, 2)) >= 18 ? 'tonight' : 'today'} ${t}`;
  if (dd === 1) return `tomorrow ${t}`;
  if (dd === -1) return `yesterday ${t}`;
  return `${DAYS[pd(iso).getDay()]} ${t}`;
}
export const atShort = (iso, asOf) => (dayDiff(iso, asOf) === 0 ? hm(iso)
  : `${DAYS[pd(iso).getDay()]} ${hm(iso)}`);

export function px(v) {
  if (v === null || v === undefined) return DASH;
  if (v >= 10) return String(Math.round(v * 10) / 10);
  const s = String(+Number(v).toFixed(2));
  return s.includes('.') ? s : `${s}.0`;
}
export const sgn = (v) => `${v > 0 ? '+' : v < 0 ? MINUS : '±'}${Math.abs(v).toFixed(1)}%`;
export const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
export const tFromUrl = (u) => { const m = /[?&]t=(\d+)s/.exec(u || ''); return m ? Number(m[1]) : null; };
export const money = (v) => (v === null || v === undefined ? DASH
  : `$${Math.round(v).toLocaleString('en-US')}`);
export const moneyS = (v) => (v === null || v === undefined ? DASH
  : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : `$${Math.round(v / 1e3)}K`);
export const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
/** 1st, 2nd, 3rd, 11th; a dash for a horse with no placing. */
export function ordinal(n) {
  if (n === null || n === undefined) return DASH;
  const teen = n % 100 >= 11 && n % 100 <= 13;
  return `${n}${teen ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th')}`;
}
export const shortDate = (iso) => `${Number(iso.slice(8, 10))} ${MON[Number(iso.slice(5, 7)) - 1]}`;

/** Quotes, as the renderer folds them: verbatim, with the second they were
 *  said (or "page" for a written source). */
export function words(list) {
  return (list || []).filter((w) => w && w.text).map((w) => {
    const t = w.t !== null && w.t !== undefined ? w.t : tFromUrl(w.url);
    return { text: w.text, en: w.en || null, url: w.url || null,
             tl: t !== null ? clock(t) : 'page', long: w.text.length > FOLD_AT };
  });
}

/* ── the three kinds, and the marks ─────────────────────────────────────── */

export function kindTag(x) {
  if (x.kind === 'computed') return { tag: 'SCREEN', tone: 'screen' };
  if (x.kind === 'said') return { tag: 'SAID', tone: x.key === 'interview' ? 'voice' : 'tip' };
  return { tag: 'PRICE', tone: 'price' };
}

/** A pick with its rank, a featured segment, an interview — three kinds of
 *  support that must not look alike — or a voice shown and not counted. */
export function mark(b) {
  const ab = AB[b.source] || b.source.toUpperCase();
  if (b.kind === 'connections') {
    return { t: `INT ${b.role === 'trainer' ? 'T' : 'J'}`, tone: 'voice', heard: !!b.heard };
  }
  if (!COUNTED.has(b.source)) {
    return { t: ab + (b.rank ? ` #${b.rank}` : ''), tone: 'shown', heard: !!b.heard };
  }
  if (b.kind === 'featured') return { t: `${ab} FEAT`, tone: 'feat', heard: !!b.heard };
  return { t: ab + (b.rank ? ` #${b.rank}` : ''), tone: 'pick', heard: !!b.heard };
}
