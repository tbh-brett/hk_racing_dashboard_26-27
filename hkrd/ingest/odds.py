"""Live odds — win, place, and the quinella matrices.

Odds live on bet.hkjc.com and are rendered by JavaScript, so the fetch needs a
real browser. That is the one sanctioned use of Playwright in this codebase;
everything else is plain HTTP. Parsing is kept separate from fetching so the
shapes can be tested without one.

The rule that governs this module: NOTHING here ever deletes a snapshot. The
old scraper called prune_old_snapshots(keep=20) after every capture, and 17
meetings survived an entire season. Odds movement is the most informative
signal in the dataset -- the favourite changes between morning and post time in
44% of races -- and it is the only thing here that cannot be reconstructed after
the fact. A season is a few hundred megabytes.
"""
from __future__ import annotations

import re
from datetime import datetime
from collections.abc import Sequence
from typing import Any

__all__ = ["OddsError", "fetch_race", "fetch_meeting",
           "parse_snapshot", "snapshot_rows", "pair_rows",
           "BET_URL"]

BET_URL = "https://bet.hkjc.com/en/racing"

_NUMERIC = re.compile(r"^\d+(\.\d+)?$")


class OddsError(ValueError):
    """A snapshot could not be read. Names what was wrong."""


def _odds(value: Any) -> float | None:
    """A price, or None where none was offered.

    Scratched runners and pre-market races show '---' or blank; those are real
    answers and must not become zero.
    """
    s = str(value or "").strip()
    if not s or not _NUMERIC.match(s):
        return None
    v = float(s)
    return v if v > 0 else None


def _horse_no(value: Any) -> int | None:
    s = str(value or "").strip()
    return int(s) if s.isdigit() else None


def parse_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise one captured payload.

    Accepts the shape the live scraper produces: win/place under `odds`, and
    pair matrices under `qin_odds` / `qpl_odds`.
    """
    date = str(payload.get("date") or "").strip()
    race_no = payload.get("race_no")
    if not date or race_no is None:
        raise OddsError(f"snapshot missing date or race_no: {list(payload)[:6]}")

    captured = str(payload.get("scraped_at") or "").strip()
    if not captured:
        raise OddsError(f"{date} R{race_no}: snapshot has no scraped_at timestamp")
    # A snapshot without a trustworthy timestamp is worthless: the whole value
    # of this table is knowing WHEN a price was true.
    try:
        datetime.fromisoformat(captured)
    except ValueError:
        raise OddsError(f"{date} R{race_no}: unparseable scraped_at {captured!r}") from None

    return {
        "race_date": date,
        "race_no": int(race_no),
        "venue": payload.get("venue"),
        "captured_at": captured,
        "runners": payload.get("odds") or [],
        "qin": payload.get("qin_odds") or [],
        "qpl": payload.get("qpl_odds") or [],
    }


def snapshot_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Win and place prices, one row per runner."""
    out: list[dict[str, Any]] = []
    for r in snapshot["runners"]:
        no = _horse_no(r.get("no"))
        if no is None:
            continue
        out.append({
            "race_date": snapshot["race_date"], "race_no": snapshot["race_no"],
            "horse_no": no, "captured_at": snapshot["captured_at"],
            "win_odds": _odds(r.get("win")), "place_odds": _odds(r.get("place")),
        })
    return out


def pair_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Quinella and quinella-place matrices.

    Pairs are stored with horse_a < horse_b so a pair has one representation,
    not two that can disagree.
    """
    out: list[dict[str, Any]] = []
    for pool, entries in (("QIN", snapshot["qin"]), ("QPL", snapshot["qpl"])):
        for e in entries:
            a, b = _horse_no(e.get("a")), _horse_no(e.get("b"))
            if a is None or b is None or a == b:
                continue
            lo, hi = (a, b) if a < b else (b, a)
            out.append({
                "race_date": snapshot["race_date"], "race_no": snapshot["race_no"],
                "pool": pool, "horse_a": lo, "horse_b": hi,
                "captured_at": snapshot["captured_at"], "odds": _odds(e.get("odds")),
            })
    return out


# ── fetching ─────────────────────────────────────────────────────────────────
# Adapted from the legacy `scrape_hkjc_live_odds.py`, which had been schema
# stable for eight months. Three things were deliberately NOT carried over:
#
#   * `prune_old_snapshots(keep=20)`, which is why 17 meetings of a full season
#     survived. AGENTS.md forbids it and nothing here deletes.
#   * the `except Exception: pass` blocks around every wait. A wait that times
#     out is recorded on the snapshot as a note; it is never silent.
#   * returning an empty row list when the header is missing. A parser that
#     cannot find the shape it was written for RAISES -- the corunning lesson.
#
# What was carried over unchanged is the stale-DOM fingerprint guard. Without
# it, races 3-9 all latch onto the R1/R2 horse list when a meeting is scraped
# in quick succession, because the page is a single-page app and the odds table
# is still the previous race's while the route settles.

_WPQ_URL = "https://bet.hkjc.com/en/racing/wpq/{date}/{venue}/{race_no}"

# The QIN/QPL pair odds render as a triangular matrix packed into a roughly
# square table, so the grid is rebuilt from cell bounding boxes rather than
# from row/column indices. Reading it positionally is what the corunning lesson
# warns against, and this reads the axis LABELS out of the rendered grid.
_MATRIX_JS = r"""
() => {
  const leaves = Array.from(document.querySelectorAll('table'))
    .filter(t => t.querySelectorAll('table').length === 0);
  const matrices = [];
  for (const t of leaves) {
    const txt = (t.innerText || '');
    if (!/Quinella/i.test(txt)) continue;
    const head = txt.trim().split(/\n+/)[0].trim();
    const label = /^Quinella Place/i.test(head) ? 'qpl'
                : (/^Quinella$/i.test(head) ? 'qin' : null);
    if (!label) continue;

    const cells = [];
    for (const c of t.querySelectorAll('th,td')) {
      const r = c.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      cells.push({ cx: Math.round(r.left + r.width / 2),
                   cy: Math.round(r.top + r.height / 2),
                   v: (c.innerText || '').trim() });
    }
    if (!cells.length) continue;

    const rowYs = [], colXs = [];
    for (const c of cells) {
      if (!rowYs.some(y => Math.abs(y - c.cy) < 8)) rowYs.push(c.cy);
      if (!colXs.some(x => Math.abs(x - c.cx) < 8)) colXs.push(c.cx);
    }
    rowYs.sort((a, b) => a - b); colXs.sort((a, b) => a - b);
    const nR = rowYs.length, nC = colXs.length;
    const at = (arr, v) => { for (let i = 0; i < arr.length; i++)
      if (Math.abs(arr[i] - v) < 8) return i; return -1; };
    const grid = Array.from({ length: nR }, () => Array(nC).fill(''));
    for (const c of cells) {
      const r = at(rowYs, c.cy), col = at(colXs, c.cx);
      if (r >= 0 && col >= 0 && grid[r][col] === '') grid[r][col] = c.v;
    }

    const isNo = v => /^\d+$/.test(v) && +v >= 1 && +v <= 20;
    const headerMap = {}, upperRow = {}, lowerRow = {}, lowerCol = {};
    for (let col = 0; col < nC; col++) if (isNo(grid[0][col])) headerMap[col] = +grid[0][col];
    for (let r = 1; r < nR; r++) {
      if (isNo(grid[r][nC - 1])) upperRow[r] = +grid[r][nC - 1];
      if (isNo(grid[r][0])) lowerRow[r] = +grid[r][0];
    }
    for (let i = 1; i < Math.min(nR, nC); i++) if (isNo(grid[i][i])) lowerCol[i] = +grid[i][i];

    const labels = new Set();
    for (const col in headerMap) labels.add(`0,${col}`);
    for (const r in upperRow) labels.add(`${r},${nC - 1}`);
    for (const r in lowerRow) labels.add(`${r},0`);
    for (const i in lowerCol) labels.add(`${i},${i}`);
    const upperDiag = {};
    for (let r = 1; r < nR; r++) {
      if (!(r in upperRow)) continue;
      for (let cc = 0; cc < nC - 1; cc++) {
        if (grid[r][cc] === String(upperRow[r]) && !labels.has(`${r},${cc}`)) {
          upperDiag[r] = cc; labels.add(`${r},${cc}`); break;
        }
      }
    }

    const pairs = {};
    for (let r = 1; r < nR; r++) {
      const ud = upperDiag[r] !== undefined ? upperDiag[r] : -1;
      for (let col = 0; col < nC; col++) {
        if (labels.has(`${r},${col}`)) continue;
        const v = grid[r][col];
        if (!/^\d+(\.\d+)?$/.test(v)) continue;
        const n = +v; if (n <= 0 || n > 9999) continue;
        let a = null, b = null;
        if (ud >= 0 && col > ud && (col in headerMap)) { a = upperRow[r]; b = headerMap[col]; }
        else if ((r in lowerRow) && (col in lowerCol) && col < r) { a = lowerRow[r]; b = lowerCol[col]; }
        if (a === null || b === null || a === b) continue;
        const lo = Math.min(a, b), hi = Math.max(a, b);
        if (!(`${lo}-${hi}` in pairs)) pairs[`${lo}-${hi}`] = { a: String(lo), b: String(hi), odds: v };
      }
    }
    if (Object.keys(pairs).length >= 5 && !matrices.some(m => m.label === label)) {
      matrices.push({ label, pairs: Object.values(pairs) });
    }
  }
  return matrices;
}
"""


def _rows_from_body(body_text: str) -> tuple[str, str, list[dict[str, str]]]:
    """Win and place per runner, read out of the rendered panel text.

    Raises if the `No. ... Horse Name ... Win` header is not present. An empty
    list would be indistinguishable from a race with no market, and a scraper
    that cannot find its own header has not read the page it thinks it has.
    """
    lines = [ln.strip() for ln in body_text.splitlines()]
    last_update = next((ln for ln in lines if ln.startswith("Last Update")), "")
    race_info = next(
        (ln for ln in lines
         if re.match(r"\d{2}/\d{2},\s+\w+,\s+\d{2}:\d{2}", ln)), "")

    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("No.") and "Horse Name" in ln and "Win" in ln),
                 None)
    if start is None:
        raise OddsError(
            "win/place header not found in the rendered page — the shape "
            "changed, or the page had not finished rendering")

    rows: list[dict[str, str]] = []
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        if ln in ("Add", "Investment Calculator", "F") or ln.startswith(
                ("Total ", "Field")):
            break
        if ln.isdigit() and 1 <= int(ln) <= 20:
            horse, nums, j = "", [], i + 1
            while j < len(lines) and len(nums) < 2:
                tok = lines[j]
                if tok:
                    if tok.isdigit() and 1 <= int(tok) <= 20 and not nums and not horse:
                        break
                    if _NUMERIC.match(tok):
                        nums.append(tok)
                    elif re.search(r"[A-Za-z]", tok) and tok not in ("Banker", "Sel."):
                        horse = horse or tok
                j += 1
            rows.append({"no": ln, "horse": horse,
                         "win": nums[0] if nums else "",
                         "place": nums[1] if len(nums) > 1 else ""})
            i = j
            continue
        i += 1
    return last_update, race_info, rows


def _fingerprint(body_text: str) -> str:
    """Race info, first horse and field size — enough to tell two races apart."""
    try:
        info, _, rows = "", "", []
        _, info, rows = _rows_from_body(body_text)
    except OddsError:
        return ""
    return f"{info}||{rows[0]['horse'] if rows else ''}||{len(rows)}"


def fetch_race(page, date: str, venue: str, race_no: int, *,
               previous: str | None = None) -> dict[str, Any]:
    """One race's win, place, QIN and QPL odds from the public /wpq/ page.

    `page` is a Playwright page — the one sanctioned use of a browser in this
    codebase, because these odds are rendered by JavaScript. `previous` is the
    fingerprint returned for the race scraped before this one; supplying it is
    what stops a whole meeting recording race 1's runners.

    Returns the payload `parse_snapshot` consumes, plus a `_fingerprint` for
    the next call and a `notes` list naming anything that did not render.
    """
    url = _WPQ_URL.format(date=date, venue=venue, race_no=race_no)
    notes: list[str] = []

    def settle() -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        for selector, what in (("text=Horse Name", "win/place table"),
                               ("text=Quinella Place", "pair matrices")):
            try:
                page.wait_for_selector(selector, timeout=15_000)
            except Exception as exc:                    # noqa: BLE001 - recorded
                # Not fatal: the extraction below either finds the shape or
                # raises. What must never happen is this passing silently.
                notes.append(f"{what} did not render within 15s ({type(exc).__name__})")
        page.wait_for_timeout(2_500)

    settle()

    # Stale-DOM guard. The site is a single-page app: after routing to a new
    # race the previous race's table can still be on screen. Poll, then reload.
    if previous:
        waited = 0
        while waited < 12_000:
            if _fingerprint(page.locator("body").inner_text(timeout=2_000)) != previous:
                break
            page.wait_for_timeout(750)
            waited += 750
        else:
            notes.append("stale DOM persisted for 12s — forced a reload")
            settle()

    body = page.locator("body").inner_text(timeout=5_000)
    last_update, race_info, runners = _rows_from_body(body)

    matrices = page.evaluate(_MATRIX_JS) or []
    pools = {m.get("label"): m.get("pairs") or [] for m in matrices}
    for pool in ("qin", "qpl"):
        if not pools.get(pool):
            notes.append(f"{pool} matrix did not render")

    snap: dict[str, Any] = {
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "date": date, "venue": venue, "race_no": race_no, "url": url,
        "last_update": last_update, "race_info": race_info,
        "n_runners": len(runners), "odds": runners,
        "qin_odds": pools.get("qin", []), "qpl_odds": pools.get("qpl", []),
        "notes": notes,
    }
    snap["_fingerprint"] = (
        f"{race_info}||{runners[0]['horse'] if runners else ''}||{len(runners)}")
    if previous and snap["_fingerprint"] == previous:
        # Reported, never swallowed: the caller decides whether to keep it.
        snap["stale_dom"] = True
        notes.append("fingerprint still matches the previous race after a reload")
    return snap


def fetch_meeting(date: str, venue: str, races: Sequence[int], *,
                  headless: bool = True,
                  executable_path: str | None = None) -> list[dict[str, Any]]:
    """Every race of one meeting, in order, through a single browser.

    Playwright is imported here rather than at module scope so that parsing,
    which is the half that gets tested, never needs a browser installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OddsError(
            "live odds need a browser, which is an optional extra: install it "
            'with `pip install -e ".[odds]"` then `playwright install chromium`. '
            "Every other page in the dashboard works without it."
        ) from exc

    out: list[dict[str, Any]] = []
    launch: dict[str, Any] = {"headless": headless}
    if executable_path:
        launch["executable_path"] = executable_path
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(**launch)
        except Exception as exc:                    # noqa: BLE001 - re-raised
            # Playwright installed but no browser downloaded is a different
            # mistake from Playwright missing, and it has a different fix.
            raise OddsError(
                "playwright is installed but has no browser to drive: run "
                "`playwright install chromium`, or pass --chromium with the "
                f"path to one. ({exc.__class__.__name__})") from exc
        try:
            page = browser.new_page()
            previous: str | None = None
            for race_no in races:
                snap = fetch_race(page, date, venue, race_no, previous=previous)
                previous = snap["_fingerprint"]
                out.append(snap)
        finally:
            browser.close()
    return out
