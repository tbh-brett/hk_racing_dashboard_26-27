"""What changed about a runner's gear, and what it has been schooled in.

Two questions, and the dashboard answered neither on the page where they are
asked. `derive/gear.py` reads HKJC's own suffix — B1 is blinkers first time,
B- is blinkers off — which was sitting in the data unread. This adds the two
things the suffix cannot say, both of which need the record rather than the
string:

**RE-INSTATED.** HKJC's `1` means first time EVER. A horse that wore blinkers,
had them taken off, and has them back on is a plain `B` again, indistinguishable
in the string from a horse that has worn them for two seasons. The archive can
tell them apart and nothing was asking it to.

**TRIALLED, NOT DECLARED.** The owner's own observation, and the reason this
module exists at all:

    "prior to using the blinker (for the first time) in a race horses are
     subjected to blinker trials, sometimes trainers would trial horses with
     blinkers ... but won't put them on in the immediate next race despite
     (potentially) improved performance"

That is a schooling step made in public and thrown away. HKJC publishes the
gear worn in a barrier trial — 3,612 of 7,787 trial rows carry one, and
blinkers are far and away the commonest at 1,214 — so a horse that trialled in
blinkers and is NOT wearing them today is a fact the card can state. It is not
a tip: it says the stable has been preparing something that has not happened
yet, which is a reason to keep watching the horse rather than to back it now.
The complement is the stronger signal and the same query answers it — trialled
in blinkers AND wearing them today for the first time, which is the schooling
step and the commitment landing together.

WHAT IS NOT CLAIMED. Nothing here scores gear or predicts from it. The archive
holds one season of trial gear and the honest test — do first-time blinkers
after a blinker trial outperform first-time blinkers without one — needs more
than that. This surfaces the fact; `model/` can have it when there is enough of
it to fit on.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.gear import FOCUS_CODES, describe, parse_pieces, pieces_by_code
from hkrd.store.connect import Connection, get_conn

__all__ = ["for_race", "for_horses", "trial_school", "TRIAL_LOOKBACK_DAYS"]

# How far back a barrier trial still counts as preparation for today's run.
# Trials are run in the weeks before a campaign, and HKJC's own trial archive
# spans 155 days across a season; six weeks covers the gap between a schooling
# trial and the race it was for without reaching back into a previous
# preparation, which would report a horse as freshly schooled in gear it has
# since raced in twice.
TRIAL_LOOKBACK_DAYS = 42


def _prev_run(conn: Connection, names: list[str], before: str
              ) -> dict[str, dict[str, Any]]:
    """Each horse's most recent run before `before`, with its gear."""
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT horse_name, race_date, race_no, gear FROM runners r "
        f"WHERE horse_name IN ({marks}) AND race_date < ? "
        f"  AND race_date = (SELECT max(race_date) FROM runners x "
        f"                    WHERE x.horse_name = r.horse_name "
        f"                      AND x.race_date < ?)",
        [*names, before, before]).fetchall()
    return {r["horse_name"]: dict(r) for r in rows}


def _ever_worn(conn: Connection, names: list[str], before: str
               ) -> dict[str, set[str]]:
    """Every gear CODE each horse has raced in before `before`.

    Codes, not raw tokens: `B`, `B1` and `B2` are the same piece of gear in
    three states, and a set of raw tokens would report blinkers as never worn
    by a horse whose only previous start was `B1`.
    """
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    out: dict[str, set[str]] = {}
    for r in conn.execute(
            f"SELECT horse_name, gear FROM runners "
            f"WHERE horse_name IN ({marks}) AND race_date < ? AND gear IS NOT NULL",
            [*names, before]):
        codes = out.setdefault(r["horse_name"], set())
        for p in parse_pieces(r["gear"]):
            if p.worn:
                codes.add(p.code)
    return out


def trial_school(conn: Connection, names: list[str], *, before: str,
                 since: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Gear worn in barrier trials in the run-up to today, per horse.

    `since` bounds how far back a trial counts as preparation; it defaults to
    `TRIAL_LOOKBACK_DAYS` before the race. Trials with no gear recorded are not
    evidence of a horse trialling bare — HKJC leaves the column empty on 54% of
    rows — so they contribute nothing rather than an absence.
    """
    if not names:
        return {}
    if since is None:
        import datetime as dt
        try:
            since = (dt.date.fromisoformat(before[:10])
                     - dt.timedelta(days=TRIAL_LOOKBACK_DAYS)).isoformat()
        except ValueError:
            since = None

    marks = ",".join("?" * len(names))
    sql = (f"SELECT horse_name, trial_date, trial_no, venue, gear, place "
           f"FROM trials WHERE horse_name IN ({marks}) AND trial_date < ? "
           f"  AND gear IS NOT NULL AND gear != ''")
    params: list[Any] = [*names, before]
    if since:
        sql += " AND trial_date >= ?"
        params.append(since)
    sql += " ORDER BY horse_name, trial_date DESC"

    out: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(sql, params):
        for p in parse_pieces(r["gear"]):
            if not p.worn:
                continue
            out.setdefault(r["horse_name"], []).append({
                "code": p.code, "name": p.name,
                "trial_date": r["trial_date"], "trial_no": r["trial_no"],
                "venue": r["venue"], "place": r["place"],
            })
    return out


def for_horses(names: list[str], declared: dict[str, str | None], *,
               before: str, conn: Connection | None = None
               ) -> dict[str, dict[str, Any]]:
    """The gear story for each horse: what HKJC flagged, and what the record adds.

    `declared` is today's gear string per horse — passed in rather than looked
    up, because the caller already has the card and a second read of it is a
    second chance for the two to disagree.
    """
    clean = [n.strip().upper() for n in names if n]
    if not clean:
        return {}
    own = conn is None
    conn = conn or get_conn()
    try:
        prev = _prev_run(conn, clean, before)
        ever = _ever_worn(conn, clean, before)
        schooled = trial_school(conn, clean, before=before)

        out: dict[str, dict[str, Any]] = {}
        for name in clean:
            today = pieces_by_code(declared.get(name))
            run = prev.get(name) or {}
            # A NULL gear column is "this scrape did not carry gear", never
            # "the horse wore nothing" — the results write erased it for
            # April to July 2026 and July has 0 of 641 runs on record. Comparing
            # against it would report every piece of gear as re-instated and
            # every horse as newly schooled, which is what it did.
            last_known = bool(run.get("gear"))
            last = pieces_by_code(run.get("gear"))
            worn_last = {c for c, p in last.items() if p.worn}
            worn_ever = ever.get(name, set())

            pieces = []
            for code, p in today.items():
                # RE-INSTATED: on today, off last start, and worn at some point
                # before that. HKJC writes a plain `B` for this, identical to a
                # horse that has worn them every start for two seasons.
                reinstated = (last_known and p.worn and p.state == "on"
                              and code not in worn_last and code in worn_ever)
                pieces.append({
                    "code": code, "name": p.name, "state": p.state,
                    "raw": p.raw, "worn": p.worn,
                    "reinstated": reinstated,
                    "notable": p.notable or reinstated,
                    "detail": (f"{p.name} back on, off last start"
                               if reinstated else describe(p)),
                })

            # Gear the horse wore LAST start and is not wearing now, where
            # HKJC did not itself flag the removal. The `B-` case is already a
            # piece above; this catches a card that simply drops the code.
            for code, p in last.items():
                if last_known and p.worn and code not in today:
                    pieces.append({
                        "code": code, "name": p.name, "state": "off",
                        "raw": p.raw, "worn": False, "reinstated": False,
                        "notable": True,
                        "detail": f"{p.name} removed since last start",
                    })

            trials = schooled.get(name, [])
            out[name] = {
                "horse_name": name,
                "declared": declared.get(name),
                "last_run": run or None,
                # Said out loud, because "nothing changed" and "we cannot tell
                # what changed" look identical on a card and are not the same.
                "comparable": last_known,
                "pieces": sorted(pieces, key=lambda p: (not p["notable"],
                                                        p["code"])),
                "trial_gear": _trial_signals(trials, today, worn_ever),
            }
        return out
    finally:
        if own:
            conn.close()


def _trial_signals(trials: list[dict[str, Any]], today: dict[str, Any],
                   worn_ever: set[str]) -> list[dict[str, Any]]:
    """What the barrier trials say about gear, one entry per piece.

    Only the headgear family — see `derive.gear.FOCUS_CODES`. A tongue tie in a
    trial is a breathing aid, not a schooling step, and reporting it beside a
    blinker trial would bury the one signal this exists for under the noise of
    the commonest piece of gear in racing.
    """
    seen: dict[str, dict[str, Any]] = {}
    for t in trials:
        if t["code"] not in FOCUS_CODES:
            continue
        # The most recent trial in that piece; the list arrives newest first.
        seen.setdefault(t["code"], t)

    out = []
    for code, t in sorted(seen.items()):
        piece = today.get(code)
        declared = bool(piece and piece.worn)
        # Measured against the WHOLE record, not the previous run. "Never
        # raced in it" survives a month of missing gear columns; "not in its
        # last start" does not, and July 2026 has none at all.
        new_today = declared and (code not in worn_ever
                                  or (piece is not None
                                      and piece.state == "first"))
        out.append({
            "code": code, "name": t["name"],
            "trial_date": t["trial_date"], "trial_no": t["trial_no"],
            "venue": t["venue"], "trial_place": t["place"],
            "declared_today": declared,
            "new_today": new_today,
            # Three readings, and only two of them are worth a chip.
            "signal": ("applied" if new_today else
                       "carried" if declared else "withheld"),
            "detail": (
                f"trialled in {t['name']} on {t['trial_date']} and wearing "
                f"them for the first time today"
                if new_today else
                f"trialled in {t['name']} on {t['trial_date']}, already worn"
                if declared else
                f"trialled in {t['name']} on {t['trial_date']} and NOT "
                f"declared today — schooled, not yet committed to"),
        })
    return out


def for_race(date: str, race_no: int, *, conn: Connection | None = None
             ) -> dict[int, dict[str, Any]]:
    """The same, keyed by horse number, for one race's declared field."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT horse_no, horse_name, gear FROM runners "
            "WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchall()
        if not rows:
            return {}
        declared = {r["horse_name"]: r["gear"] for r in rows}
        found = for_horses([r["horse_name"] for r in rows], declared,
                           before=date, conn=conn)
        return {r["horse_no"]: found[r["horse_name"]]
                for r in rows if r["horse_name"] in found}
    finally:
        if own:
            conn.close()
