/* live.js — keep a page in step with a market that moves under it.
 *
 * The odds are captured every minute through the last ten minutes before a
 * race. A page that reads them once at load is showing, twenty minutes later,
 * the price from twenty minutes ago — which is exactly the window brief 01
 * says Race Day exists for. So the two pages that read a live price poll.
 *
 * Three things stop that being expensive, and none of them live here:
 *
 *   The server decides the interval. Every reply carries `X-Poll-After`,
 *   computed from the same cadence ladder the capture runs on — half the
 *   capture interval for that race. Overnight that is thirty minutes; inside
 *   the last ten minutes it is fifteen seconds. One schedule, and it lives
 *   with the data rather than being guessed at twice.
 *
 *   A poll that finds nothing new is a 304 with no body, answered from two
 *   indexed lookups. The 36 KB card is assembled only when it changed.
 *
 *   A hidden tab does not poll at all. A card left open on another desktop
 *   overnight would otherwise ask a few hundred pointless questions, and the
 *   first thing anyone does on coming back is look — so it polls immediately
 *   on becoming visible, which is both cheaper and fresher.
 *
 * On failure it backs off rather than hammering: a server that is down does
 * not get a request a second from every open tab, and the page keeps the last
 * price it had rather than blanking. What it must never do is fail silently,
 * so the caller is handed the error to put on screen.
 */

const MIN_SECONDS = 15;
const MAX_BACKOFF_SECONDS = 300;

/** One watched resource. Call `.watch(key, path)` to point it somewhere new. */
export class Live {
  /**
   * @param {(body:object)=>void} onFresh   a changed payload arrived
   * @param {(message:string)=>void} [onError]  a poll failed
   */
  constructor(onFresh, onError) {
    this.onFresh = onFresh;
    this.onError = onError ?? (() => {});
    this.timer = null;
    this.key = null;
    this.path = null;
    this.etag = null;
    this.failures = 0;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.#cancel();
      else if (this.path) this.#schedule(0);
    });
  }

  /** Point at a new resource. `key` identifies it; a repeat is a no-op, so a
   *  re-render that re-declares the same watch does not restart the clock. */
  watch(key, path) {
    if (key === this.key) return;
    this.#cancel();
    this.key = key;
    this.path = path;
    // The ETag belongs to the OLD resource. Carrying it over would ask
    // "changed since?" about a different race and take a 304 for an answer.
    this.etag = null;
    this.failures = 0;
  }

  /** Record the tag and interval from a payload the PAGE fetched itself, so
   *  the first poll after a load is a conditional one rather than a second
   *  full download of what is already on screen. */
  seed(etag, pollAfter) {
    this.etag = etag ?? null;
    this.#schedule(pollAfter ?? 60);
  }

  stop() { this.#cancel(); this.path = null; this.key = null; }

  #cancel() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  #schedule(seconds) {
    this.#cancel();
    if (!this.path || document.hidden) return;
    this.timer = setTimeout(() => this.#poll(), Math.max(0, seconds) * 1000);
  }

  async #poll() {
    const path = this.path;
    try {
      const res = await fetch(`/api${path}`, {
        // Our own conditional request, not the browser's. With the browser
        // cache in play a 304 can be turned back into a 200 from cache before
        // JS ever sees it, and then "unchanged" and "changed" look identical.
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
          ...(this.etag ? { 'If-None-Match': this.etag } : {}),
        },
      });
      if (path !== this.path) return;          // watched something else mid-flight
      this.failures = 0;
      const after = Number(res.headers.get('X-Poll-After')) || 60;
      const tag = res.headers.get('ETag');
      if (tag) this.etag = tag;
      if (res.status === 304) { this.#schedule(after); return; }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const body = await res.json();
      this.onFresh(body);
      this.#schedule(body.poll_after ?? after);
    } catch (e) {
      if (path !== this.path) return;
      this.failures += 1;
      this.onError(e.message);
      // Doubling, capped. A server that is down must not be asked once a
      // second by every tab someone left open.
      this.#schedule(Math.min(MIN_SECONDS * 2 ** this.failures,
                              MAX_BACKOFF_SECONDS));
    }
  }
}
