/* horse-search.js — a name dropping down as you type, attached to an input.
 *
 * The command palette already does this for the whole app, and the Trials page
 * asked for the same feel without leaving the page. Rather than a second copy
 * of the matching and keyboard handling inside `trials.js` — which is already
 * past the file cap — it is one small module that takes an input, a fetcher
 * and a callback.
 *
 * The FETCHER is a parameter on purpose. The palette searches `runners`; the
 * Trials page has to search `trials`, because 107 horses in the archive have
 * trialled and never raced and an index built from runs cannot see one of
 * them. Same component, different index.
 */
import { el } from './vocab.js';

/** Attach a typeahead to `input`.
 *
 *  fetcher(query) -> Promise<[{ horse_name, ... }]>
 *  onPick(horse)  -> called with the chosen row
 *  label(horse)   -> optional right-hand hint text
 */
export function attachHorseSearch(input, { fetcher, onPick, label = null,
                                           minChars = 2 } = {}) {
  const menu = el('div', 'hs-menu');
  menu.hidden = true;
  // The input sits in a positioned wrapper on both pages that use this; if it
  // ever does not, the menu still renders, just against the page instead.
  (input.parentElement ?? document.body).append(menu);

  let rows = [];
  let cursor = 0;
  let timer = null;
  let seq = 0;

  function hide() {
    menu.hidden = true;
    rows = [];
    cursor = 0;
  }

  function draw() {
    menu.replaceChildren();
    if (!rows.length) { menu.hidden = true; return; }
    rows.forEach((h, i) => {
      const row = el('button', 'hs-row');
      row.type = 'button';
      row.setAttribute('aria-selected', String(i === cursor));
      row.append(el('span', 'hs-name', h.horse_name));
      if (label) row.append(el('span', 'hs-hint', label(h)));
      // mousedown, not click: `blur` fires first on a click and would hide the
      // menu before the handler ran, so the row would look unresponsive.
      row.addEventListener('mousedown', (e) => { e.preventDefault(); pick(h); });
      menu.append(row);
    });
    menu.hidden = false;
  }

  function pick(h) {
    hide();
    onPick(h);
  }

  function search(q) {
    clearTimeout(timer);
    if (q.length < minChars) { hide(); return; }
    const mine = (seq += 1);
    timer = setTimeout(() => {
      fetcher(q).then((found) => {
        // Guarded so a slow answer for "go" cannot land after a fast one for
        // "golden" and replace the better list with the staler one.
        if (mine !== seq) return;
        rows = found ?? [];
        cursor = 0;
        draw();
      }).catch(() => hide());
    }, 140);
  }

  input.addEventListener('input', () => search(input.value.trim()));
  input.addEventListener('blur', () => setTimeout(hide, 120));
  input.addEventListener('keydown', (e) => {
    if (menu.hidden || !rows.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault(); cursor = (cursor + 1) % rows.length; draw();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault(); cursor = (cursor - 1 + rows.length) % rows.length; draw();
    } else if (e.key === 'Enter') {
      e.preventDefault(); pick(rows[cursor]);
    } else if (e.key === 'Escape') {
      hide();
    }
  });

  return { hide };
}
