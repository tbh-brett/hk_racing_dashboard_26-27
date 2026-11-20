/* desk-page.js — the Desk, a page of its own since 20 Nov 2026: choose your
 * bankers before each race, judge them after it, one race at a time.
 *
 * It left the Bets page at the owner's request, with the review he asked for
 * the same day (docs/banker-review.md): he means to do this every race night,
 * so the night is one strip of race chips and the job in each race is the one
 * its clock calls for.
 *
 *   TONIGHT   the meeting in the header, race by race (desk.js). A race still
 *             to run is the desk: your horses, the ticket, a line on what has
 *             to happen, how sure you are, TAKE or PASS. A race that has run
 *             carries its review (review-race.js): one tap per banker for what
 *             decided it, and your note on the race. Your note on the night
 *             sits under the races.
 *   BACKLOG   every banker since April, meeting by meeting, and the record so
 *             far (banker-review.js). Optional.
 *
 * BOOK picks whose bankers: BOTH, BRETT (his statements and the April-June
 * log) or KELVIN (the legs of his tickets). A desk decision is filed to the
 * book picked, Brett's when BOTH.
 */
import { el, $, renderNav, accountPicker } from './vocab.js';
import { context } from './context.js';
import { renderDesk } from './desk.js';
import { renderReview, staleReview } from './banker-review.js';
import { chipNote, firstRace, load as loadNight, nightEl, raceEl } from './review-race.js';
import { install as installPalette } from './palette.js';

const state = { view: 'tonight', account: null };
const VIEWS = [['tonight', 'TONIGHT'], ['backlog', 'BACKLOG']];

function render() {
  $('view-toggle').replaceChildren(...VIEWS.map(([key, label]) => {
    const b = el('button', null, label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.view === key));
    b.addEventListener('click', () => { state.view = key; render(); });
    return b;
  }));
  $('book').replaceChildren(accountPicker(state.account, (k) => { state.account = k; render(); },
                                          { label: 'BOOK' }));
  $('view-tonight').hidden = state.view !== 'tonight';
  $('view-backlog').hidden = state.view !== 'backlog';
  if (state.view === 'tonight') {
    loadNight(context.date, state.account, { redraw: render, changed: staleReview });
    renderDesk($('desk-host'), context.date, {
      account: state.account || 'brett', afterRace: raceEl, chipNote, firstRace });
    $('night-host').replaceChildren(nightEl());
  } else {
    renderReview($('review-host'), { account: state.account });
  }
}

async function boot() {
  renderNav($('nav'), 'desk.html');
  installPalette();
  context.onChange((what) => {
    if (what === 'meeting' || what === 'date') render();
  });
  await context.init();
  render();
}

boot().catch((err) => {
  document.body.append(el('div', 'no-match', `FAILED TO LOAD — ${err.message}`));
});
