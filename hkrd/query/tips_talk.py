"""What a pundit's video discussed, section by section, under each runner it
names.

Brett, 30 Sep 2026: "whatever is discussed is summarised, identified and
included on the dashboard" — and then, seeing it as a panel of its own above
the races: the runner's expanded row, source and quote, is the place for it.
賽馬Fact Check's 10.1 preview spent as long on Badel's return from suspension,
羅富全 giving him three rides, 伍鵬志's six meetings in a row and 希斯's day
racing as on any one horse — talk about no one runner, which the per-horse
quotes (`tips_summary`) leave out.

The extractor keeps every section of a video as a quote on no runner, `topic`
'section', with the card's Chinese names of the runners it names in
`horse_said` (tools/extract_factcheck). Here each section is handed to every
runner it names, so under VICTORY CHAMPION sits the whole of the Badel
section it was named in, and under RAGING BLIZZARD — named in the opening
survey and the Cup's history, discussed in neither — the two sections, marked
on the page as naming it rather than backing it.

Names are matched whole against the meeting's card, never guessed: a name
the card does not have (a horse at another meeting, 「潮州高球」 in the 10.1
preview) stays in the text and is attached to no one.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["by_runner"]

_LABEL = {"factcheck": "賽馬Fact Check"}


def by_runner(date: str, *, conn: Connection | None = None
              ) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """(race_no, horse_no) -> every section naming that runner, in the order
    the videos say them. Empty when no section is stored for the meeting."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _by_runner(conn, date)
    finally:
        if own:
            conn.close()


def _by_runner(conn: Connection, date: str
               ) -> dict[tuple[int, int], list[dict[str, Any]]]:
    rows = conn.execute(
        "SELECT source, t_start, quote, horse_said, url FROM connections_quote "
        "WHERE race_date = ? AND topic = 'section' "
        "ORDER BY source, video_id, t_start", (date,)).fetchall()
    if not rows:
        return {}
    card = {r["name_zh"]: (r["race_no"], r["horse_no"]) for r in conn.execute(
        "SELECT u.race_no, u.horse_no, z.name_zh FROM runners u "
        "JOIN horse_name_zh z ON z.horse_name = u.horse_name "
        "WHERE u.race_date = ?", (date,))}
    out: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        section = {"source": r["source"],
                   "label": _LABEL.get(r["source"], r["source"]),
                   "t": r["t_start"], "url": r["url"], "text": r["quote"],
                   "head": re.split(r"[，。]", r["quote"], maxsplit=1)[0]}
        for name in (r["horse_said"] or "").split("、"):
            if name in card:
                out[card[name]].append(section)
    return dict(out)
