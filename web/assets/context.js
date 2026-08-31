/* context.js — Layer 1. The meeting, chosen once, obeyed everywhere.
 *
 * Design brief 08 §1 names the fault this fixes: "the top-left button changing
 * identity between pages, the date colliding with it, and the mobile toggle
 * appearing where it shouldn't are ALL THE SAME FAULT — page-level content has
 * leaked into global chrome." Brief 01 puts it as a requirement: "Persistent
 * header — meeting date and venue, chosen once. Every part of the page obeys
 * it." PROMPTS.md Phase 4 states the negative form: "No page has its own date
 * picker."
 *
 * The first build gave four pages three different date pickers and no shared
 * state, which is the old dashboard's shape. This module is the whole of the
 * replacement: one context, one header, one URL.
 *
 * It is also addressable. `?date=2026-07-15&race=3` restores the view, so a bet
 * can link back to the race that produced it and a test can drive the UI by URL
 * (brief §3.4, the Datasette point).
 */
import { api } from './api.js';
import { el } from './vocab.js';

const LAYER_1 = '.chrome-meeting';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** One format, defined once, used everywhere: `15 Jul 2026` (brief 08 §1). */
export function formatMeetingDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? '');
  if (!m) return iso ?? '—';
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}


class MeetingContext {
  constructor() {
    this.date = null;
    this.race = null;
    this.meetings = [];
    this.freshness = null;
    this.summary = null;
    this.status = null;
    this._listeners = new Set();
  }

  get meeting() {
    return this.meetings.find((m) => m.race_date === this.date) ?? null;
  }

  get races() {
    return this.summary?.races ?? [];
  }

  /** Subscribe. Returns an unsubscribe so a page can tear down cleanly. */
  onChange(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  _emit(what) {
    this._listeners.forEach((fn) => fn(this, what));
  }

  /** The URL is the state, not a copy of it — so a reload restores the view
   *  and a link carries it. replaceState rather than pushState: moving between
   *  races is not navigation, and filling the back stack with it would make
   *  the back button useless for leaving the page. */
  _writeUrl() {
    const url = new URL(window.location.href);
    if (this.date) url.searchParams.set('date', this.date);
    else url.searchParams.delete('date');
    if (this.race) url.searchParams.set('race', String(this.race));
    else url.searchParams.delete('race');
    window.history.replaceState(null, '', url);
  }

  static _readUrl() {
    const p = new URLSearchParams(window.location.search);
    const date = p.get('date');
    const race = Number(p.get('race'));
    return {
      date: /^\d{4}-\d{2}-\d{2}$/.test(date ?? '') ? date : null,
      race: Number.isInteger(race) && race > 0 ? race : null,
    };
  }

  /** Move to a meeting. `race` defaults to the first of that meeting rather
   *  than being carried across — race 9 of one card is not race 9 of another. */
  async setDate(date, { race = null } = {}) {
    if (date === this.date && (race === null || race === this.race)) return;
    this.date = date;
    this.summary = null;
    this.race = null;
    this._emit('date');                       // pages clear while it loads
    await this._loadMeeting();
    this.race = this._validRace(race) ?? this.races[0]?.race_no ?? null;
    this._writeUrl();
    this.render();
    this._emit('meeting');
  }

  setRace(race) {
    const next = this._validRace(race);
    if (next === null || next === this.race) return;
    this.race = next;
    this._writeUrl();
    this.render();
    this._emit('race');
  }

  _validRace(race) {
    if (!Number.isInteger(race)) return null;
    return this.races.some((r) => r.race_no === race) ? race : null;
  }

  async _loadMeeting() {
    try {
      this.summary = await api.raceDayMeeting(this.date);
    } catch {
      // A meeting with no card is a real state (a date typed into the URL),
      // not an error to hide. Pages see an empty race list and say so.
      this.summary = { race_date: this.date, races: [] };
    }
  }

  /** Layer 1: venue · date · race count · status. Nothing page-specific ever
   *  enters it — that leak is the fault brief 08 §1 exists to fix. */
  render() {
    const host = document.querySelector(LAYER_1);
    if (!host) return;
    host.replaceChildren();

    const brand = el('div', 'brand');
    brand.append(el('span', 'dot'));
    brand.append(document.createTextNode('RACEDAY'));
    host.append(brand);

    const meeting = this.meeting;
    const box = el('button', 'meeting-ctx');
    box.type = 'button';
    box.append(el('span', 'venue', meeting?.venue ?? '—'));
    box.append(el('span', 'sep', '·'));
    box.append(el('span', 'date', formatMeetingDate(this.date)));
    box.append(el('span', 'sep', '·'));
    box.append(el('span', 'races', `${this.races.length || meeting?.races || 0} races`));
    // The caret is what makes this read as a control. Design brief 08 §1 keeps
    // the meeting in Layer 1 and PROMPTS.md Phase 4 forbids per-page date
    // pickers, so this is the ONLY meeting selector in the app — which makes
    // it the one control that cannot afford to be invisible.
    box.append(el('span', 'caret', '▾'));
    box.title = 'change meeting — or press ⌘K';
    box.setAttribute('aria-haspopup', 'dialog');
    box.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('palette:open', { detail: { seed: '' } }));
    });
    host.append(box);

    const state = el('div', 'meeting-state');
    const latest = this.status?.latest_meeting;
    const isLatest = latest && latest === this.date;
    state.append(el('span', `pip${isLatest ? ' live' : ''}`));
    state.append(document.createTextNode(
      isLatest ? 'latest meeting' : latest ? `archive · latest is ${formatMeetingDate(latest)}` : ''));
    host.append(state);

    host.append(this._freshness());

    const hint = el('div', 'palette-hint', '⌘K');
    hint.title = 'search meetings, races, horses and pages';
    hint.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('palette:open', { detail: { seed: '' } }));
    });
    host.append(hint);
  }

  /* Per SOURCE, not per derived table.
   *
   * This strip read `/api/status` — the MODEL status endpoint — and rendered
   * its `tables` key, so it showed et/pace/sarr/tags: how much of the archive
   * has been derived. Useful, but not the question. Design brief 07 §6 asks
   * what is CURRENT: Card, Odds, Results, Trials, Vet, each judged against
   * what is normal for it, because odds go stale in minutes and trials are
   * published weekly.
   *
   * Clicking a stale source is how you refresh just that source — the brief's
   * inversion, where the system says what needs attention instead of the user
   * remembering to check.
   */
  _freshness() {
    const box = el('div', 'freshness');
    const sources = this.freshness?.sources;
    if (!sources) return box;
    sources.forEach((s) => {
      const chip = el('span', `src${s.stale ? ' is-stale' : ''}`);
      chip.append(el('span', 'name', s.name));
      chip.append(el('span', s.stale ? 'stale' : s.minutes === null ? 'never' : 'ok',
        `${s.mark}${s.minutes === null ? '' : ` ${s.age}`}`));
      // Never a bare mark: what the last run wrote travels with it, because a
      // job that ran and stored nothing is the failure this strip exists for.
      chip.title = s.minutes === null
        ? `${s.name}: no successful run on record (normal: every ${s.normal})`
        : `${s.name}: last landed ${s.age} ago, normal is every ${s.normal}`
          + (s.detail ? `\n${s.detail}` : '');
      box.append(chip);
    });
    return box;
  }

  /** Called once per page. Resolves the meeting from the URL, then the latest.
   *  Pages get a fully populated context before their first render. */
  async init() {
    const [meetings, status, freshness] = await Promise.all([
      api.meetings(60).catch(() => []),
      api.status().catch(() => null),
      api.freshness().catch(() => null),
    ]);
    this.meetings = meetings;
    this.status = status;
    this.freshness = freshness;

    const url = MeetingContext._readUrl();
    const known = (d) => meetings.some((m) => m.race_date === d);
    this.date = (url.date && known(url.date)) ? url.date
      : status?.latest_meeting ?? meetings[0]?.race_date ?? null;

    if (this.date) {
      await this._loadMeeting();
      this.race = this._validRace(url.race) ?? this.races[0]?.race_no ?? null;
    }
    this._writeUrl();
    this.render();
    // Browser back/forward should restore the view, since the URL carries it.
    window.addEventListener('popstate', () => {
      const next = MeetingContext._readUrl();
      if (next.date && next.date !== this.date) this.setDate(next.date, { race: next.race });
      else if (next.race) this.setRace(next.race);
    });
    return this;
  }
}

export const context = new MeetingContext();
