/* side-panel.js — a page's right-hand panel that opens and shuts.
 *
 * The Form Guide's fit panel and Race Day's detail panel were drawn for a desk
 * screen, where they sit beside the card and cost the card nothing. On an iPad
 * or a phone the same 256 to 296px comes out of the card, and the card is the
 * page. So the panel has two layouts, chosen by the width of the window rather
 * than by the device:
 *
 *   - 1440px and wider: docked beside the card, as drawn, and open until put
 *     away. Measured on 9 Sep 2026 R3: the Form Guide's ten columns need about
 *     1130px with the flags showing and its panel 296 more; Race Day's card
 *     squeezes to 1188px at the narrowest and its panel is 256.
 *   - narrower: laid over the card's right edge, shut until asked for, so the
 *     card keeps the whole window.
 *
 * A button in the race strip opens and shuts it in either layout; the panel's
 * own HIDE, and Esc when it is laid over the card, shut it. Each layout
 * remembers its own choice on this device, so putting the panel away on the
 * iPad does not put it away on the desk.
 *
 * The layout itself is CSS (side-panel.css), keyed to the same width, so a
 * window dragged across 1440 changes layout without this file's help; this
 * file only decides open or shut.
 */

const WIDE = '(min-width: 1440px)';

/**
 * Wire `panel` (an element holding a `.side-panel-close` button) to `toggle`.
 *
 * @param {{panel: HTMLElement, toggle: HTMLButtonElement, key: string}} args
 *   `key` names the panel in this device's storage.
 * @returns {void}
 */
export function sidePanel({ panel, toggle, key }) {
  const wide = window.matchMedia(WIDE);
  const slot = () => `hkrd:${key}:${wide.matches ? 'docked' : 'over'}`;

  const saved = () => {
    try {
      const v = localStorage.getItem(slot());
      return v === null ? null : v === 'open';
    } catch {
      return null;       // site data blocked: each layout starts as drawn
    }
  };
  const save = (open) => {
    try {
      localStorage.setItem(slot(), open ? 'open' : 'shut');
    } catch { /* nothing to remember on is not a failure */ }
  };

  const show = (open) => {
    panel.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
  };
  const choose = (open) => { save(open); show(open); };
  const layout = () => show(saved() ?? wide.matches);
  const isOpen = () => panel.classList.contains('open');

  toggle.addEventListener('click', () => choose(!isOpen()));
  panel.querySelector('.side-panel-close').addEventListener('click', () => {
    choose(false);
    toggle.focus();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !wide.matches && isOpen()) choose(false);
  });
  wide.addEventListener('change', layout);
  layout();
}
