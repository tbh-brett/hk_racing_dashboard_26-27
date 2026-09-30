"""What a pundit's video discussed, section by section, for one meeting.

Brett, 30 Sep 2026: "whatever is discussed is summarised, identified and
included on the dashboard". 賽馬Fact Check's 10.1 preview spent as long on
Badel's return from suspension, 羅富全 giving him three rides, 伍鵬志's run of
six meetings and 希斯's day racing as on any one horse — talk about no one
runner, which the per-horse quotes (`tips_summary`) leave out.

The extractor keeps every section of a video as a quote on no runner, `topic`
'section', with the card's Chinese names of the runners it names in
`horse_said` (tools/extract_factcheck). Here each section becomes:

  head     its first clause, 「講開巴度停賽期滿復出」 — what the section is about
  text     the whole of it, verbatim
  runners  every runner it names, IDENTIFIED: race, number, English name,
           jockey and trainer from the card. The page links each to its race,
           and the jockey and trainer beside the horse say who 「巴度」 or
           「伍鵬志」 is without a name table the dashboard does not have.

and the video's picks — the tipster's selections in its speech-to-text tail —
come after its sections, as the video gives them. Names are matched whole
against the meeting's card, never guessed: a name the card does not have
(a horse at another meeting, 「潮州高球」 in the 10.1 preview) stays in the
text and is simply not linked.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["talk"]

_LABEL = {"factcheck": "賽馬Fact Check"}
_VIDEO = re.compile(r"[?&]v=([\w-]{6,})")
_AT = re.compile(r"[?&]t=(\d+)s")


def _video(url: str | None) -> str | None:
    m = _VIDEO.search(url or "")
    return m.group(1) if m else None


def talk(date: str, *, conn: Connection | None = None) -> list[dict[str, Any]]:
    """One entry per video with sections stored for the meeting."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _talk(conn, date)
    finally:
        if own:
            conn.close()


def _talk(conn: Connection, date: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT source, video_id, t_start, quote, horse_said, url, fetched_at "
        "FROM connections_quote WHERE race_date = ? AND topic = 'section' "
        "ORDER BY source, video_id, t_start", (date,)).fetchall()
    if not rows:
        return []
    card: dict[str, dict[str, Any]] = {}
    by_no: dict[tuple[int, int], dict[str, Any]] = {}
    for r in conn.execute(
            "SELECT u.race_no, u.horse_no, u.horse_name, u.jockey, u.trainer, "
            "       z.name_zh FROM runners u "
            "LEFT JOIN horse_name_zh z ON z.horse_name = u.horse_name "
            "WHERE u.race_date = ? ORDER BY u.race_no, u.horse_no", (date,)):
        runner = {k: r[k] for k in ("race_no", "horse_no", "horse_name",
                                    "name_zh", "jockey", "trainer")}
        by_no[(r["race_no"], r["horse_no"])] = runner
        if r["name_zh"]:
            card[r["name_zh"]] = runner

    videos: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        vid = r["video_id"] or _video(r["url"]) or r["url"]
        v = videos.setdefault((r["source"], vid), {
            "source": r["source"], "label": _LABEL.get(r["source"], r["source"]),
            "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
            "fetched_at": r["fetched_at"], "sections": [], "picks": []})
        said = [n for n in (r["horse_said"] or "").split("、") if n]
        text = r["quote"]
        v["sections"].append({
            "t": r["t_start"], "url": r["url"],
            "head": re.split(r"[，。]", text, maxsplit=1)[0],
            "text": text,
            "runners": [card[n] for n in said if n in card],
            "unlinked": [n for n in said if n not in card]})

    # The picks in each video's tail, from the same video's url.
    by_video: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources = sorted({s for s, _ in videos})
    marks = ",".join("?" * len(sources))
    for p in conn.execute(
            f"SELECT source, tipster, race_no, horse_no, pick_rank, url, "
            f"       caption_kind FROM tipster_selection "
            f"WHERE race_date = ? AND source IN ({marks}) "
            f"ORDER BY race_no, coalesce(pick_rank, 9), horse_no",
            (date, *sources)):
        x = by_no.get((p["race_no"], p["horse_no"]))
        if x is None:
            continue
        at = _AT.search(p["url"] or "")
        by_video[_video(p["url"]) or ""].append({
            **x, "tipster": p["tipster"], "rank": p["pick_rank"],
            "heard": p["caption_kind"] == "asr", "url": p["url"],
            "t": int(at.group(1)) if at else None})
    for (source, vid), v in videos.items():
        # A ranked pick first, then in the order the tipster says them.
        v["picks"] = sorted(by_video.get(vid, []), key=lambda p: (
            p["race_no"], p["rank"] or 9, p["t"] if p["t"] is not None else 1e9))
    return sorted(videos.values(), key=lambda v: (v["label"], v["sections"][0]["t"]))
