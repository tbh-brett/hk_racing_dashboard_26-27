"""Race and horse queries. Read-only, returns RunnerLine.

Every filter is a WHERE clause. Nothing is filtered in pandas: a targeted query
against this data measures 0.000s where a full-table read measures 1.09s and the
spreadsheet path measured 15.33s.
"""
from __future__ import annotations

from collections.abc import Sequence

from hkrd.store.coerce import parse_running_positions, parse_section_times
from hkrd.store.connect import Connection, get_conn
from hkrd.derive.pace import STYLE_WINDOW, habitual_style
from hkrd.derive.tags import VET_TAGS
from hkrd.query.types import RaceLine, RunnerLine

__all__ = ["get_race", "get_meeting", "get_horse_form", "list_meetings",
           "list_horses", "latest_appearance", "tags_bulk", "vet_form",
           "habitual_styles"]

# Derived tables are LEFT JOINed: a runner with no ET row still returns, with a
# null figure. A missing derived value must never make a runner disappear.
_LINE_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, r.draw, r.jockey,
       r.trainer, r.actual_weight, r.declared_weight, r.gear, r.place,
       r.place_code, r.dead_heat, r.finish_time, r.lengths_behind,
       r.running_positions, r.section_times, r.win_odds,
       a.venue, a.course, a.surface, a.going, a.distance, a.race_class,
       a.off_time,
       e.figure AS et_figure, e.len_vs_par AS et_len_vs_par,
       e.len_vs_race AS et_len_vs_race, e.sec_vs_par AS et_sec_vs_par,
       e.et_n_eff, e.confidence AS et_confidence,
       p.pace_style, p.early_dev, p.late_dev,
       s.sarr, s.sarr_rank,
       (SELECT count(*) FROM runners f
         WHERE f.race_date = r.race_date AND f.race_no = r.race_no) AS field_size,
       -- WHAT THE WINNER WON BY. A winner has no lengths-behind -- it is not
       -- behind anything -- so the margin column was blank on exactly the runs
       -- worth reading it on, and "won" told you nothing about whether it was
       -- a nose or six lengths.
       --
       -- The margin is the RUNNER-UP's beaten lengths, which is the same fact
       -- seen from the other side. A dead heat for first has no second placing
       -- at all, so the subquery would find nothing and the margin is zero by
       -- definition; that case is answered before it is asked.
       --
       -- Correlated, and deliberately so: it runs only for the one runner in a
       -- field that won, against the (race_date, race_no) primary key. The
       -- rule about per-race constants is about recomputing one for every row
       -- of a large result -- this is a twelfth of the rows and an index seek.
       CASE
         WHEN r.place = 1 AND r.dead_heat = 1 THEN 0.0
         WHEN r.place = 1 THEN (
           SELECT min(w.lengths_behind) FROM runners w
            WHERE w.race_date = r.race_date AND w.race_no = r.race_no
              AND w.place = 2)
       END AS win_margin,
       -- The tote's PLACE payout for THIS horse, per $10. `combination` is the
       -- horse number as published, so it is compared as text on both sides
       -- rather than cast: a cast would turn an unparseable combination into a
       -- silent zero, and a dividend that reads 0 is a losing ticket.
       (SELECT d.dividend_per_10 FROM dividends d
         WHERE d.race_date = r.race_date AND d.race_no = r.race_no
           AND d.pool = 'PLACE'
           AND trim(d.combination) = cast(r.horse_no AS TEXT)) AS place_dividend,
       -- THE PLACE STARTING PRICE. `runners` has a win_odds column, written
       -- by the results scrape, and no place one — so a place price existed
       -- nowhere outside `odds_snapshots` and only Race Day ever merged it in.
       -- Every other surface printed a dash for a number that was captured.
       --
       -- The last capture IS the starting price now that the capture stops
       -- when HKJC shuts the pool rather than half an hour after the scheduled
       -- off. The placeholder 999.0 is excluded here as everywhere: an open
       -- pool nobody has bet into is not a price.
       (SELECT o.place_odds FROM odds_snapshots o
         WHERE o.race_date = r.race_date AND o.race_no = r.race_no
           AND o.horse_no = r.horse_no
           AND o.place_odds IS NOT NULL AND o.place_odds < 999
         ORDER BY o.captured_at DESC LIMIT 1) AS place_sp
FROM runners r
JOIN races a       ON a.race_date = r.race_date AND a.race_no = r.race_no
LEFT JOIN runner_et e   USING (race_date, race_no, horse_no)
LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
"""


def _tags(conn: Connection, date: str, race_no: int,
          horse_no: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (tags, lane_notes).

    Lane descriptors are stored namespaced 'lane:' so they share one table with
    trip tags while staying separable: they are an objective statement of where
    the horse travelled, not a judgement about its trip.
    """
    rows = conn.execute(
        "SELECT tag FROM runner_tags WHERE race_date=? AND race_no=? AND horse_no=? "
        "ORDER BY tag", (date, race_no, horse_no)).fetchall()
    tags, lanes = [], []
    for r in rows:
        tag = r["tag"]
        (lanes if tag.startswith("lane:") else tags).append(
            tag[5:] if tag.startswith("lane:") else tag)
    return tuple(tags), tuple(lanes)


def tags_bulk(conn: Connection, keys: Sequence[tuple[str, int, int]]
              ) -> dict[tuple[str, int, int], tuple[tuple[str, ...], tuple[str, ...]]]:
    """`_tags` for many runs at once, as {(date, race_no, horse_no): (tags, lanes)}.

    Lookup returns up to 500 rows spanning as many races. Calling `_tags` per
    row is 500 round trips for a grid that has 500ms to render, which is why
    the page shipped with no trip column at all rather than a slow one. One
    query answers the whole grid.
    """
    wanted = {(str(d), int(n), int(h)) for d, n, h in keys}
    if not wanted:
        return {}
    # Bounded by the dates actually asked for. Reading the whole table and
    # discarding the rest costs one full scan per page — 20,744 rows today and
    # another 20,744 every season, to answer about 500. The date range is the
    # cheap bound: a Lookup page spans days or months, never the archive.
    lo = min(k[0] for k in wanted)
    hi = max(k[0] for k in wanted)
    out: dict[tuple[str, int, int], tuple[list[str], list[str]]] = {}
    for r in conn.execute(
            "SELECT race_date, race_no, horse_no, tag FROM runner_tags "
            "WHERE race_date BETWEEN ? AND ? ORDER BY tag", (lo, hi)):
        key = (r["race_date"], r["race_no"], r["horse_no"])
        if key not in wanted:
            continue
        tags, lanes = out.setdefault(key, ([], []))
        tag = r["tag"]
        (lanes if tag.startswith("lane:") else tags).append(
            tag[5:] if tag.startswith("lane:") else tag)
    return {k: (tuple(t), tuple(l)) for k, (t, l) in out.items()}


def vet_form(horse_names: Sequence[str], *, before: str | None = None,
             runs: int = 6, conn: Connection | None = None
             ) -> dict[str, list[dict[str, Any]]]:
    """Veterinary findings over each horse's last `runs` starts.

    The one answer to "has this horse been found wrong lately", so that Race
    Day and the Form Guide cannot give different ones. Both pages showed the
    finding only where the reader had already gone looking: Race Day read the
    single most recent run, and the Form Guide buried it in a tooltip on a run
    row two clicks deep. A horse that bled three starts back is a fact you want
    before you open anything.

    Newest first, with how many starts ago it was and the stewards' own
    sentence, so the chip on the row can say WHAT and WHEN and the tooltip can
    show the evidence.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        return _vet_form(conn, horse_names, before, runs)
    finally:
        if own:
            conn.close()


def _vet_form(conn: Connection, horse_names: Sequence[str],
              before: str | None, runs: int) -> dict[str, list[dict[str, Any]]]:
    names = [n.strip().upper() for n in horse_names if n]
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    # The stewards' account, which is where the finding was read from. Their
    # 'incident' text is preferred over the objective corunning description:
    # the finding is in the incident report, and showing the other one under a
    # chip that says "bled" would be evidence for a different claim.
    sql = (f"SELECT r.horse_name, r.race_date, r.race_no, t.tag, "
           f"       (SELECT comment_text FROM runner_comments c "
           f"         WHERE c.race_date = r.race_date AND c.race_no = r.race_no "
           f"           AND c.horse_no = r.horse_no "
           f"         ORDER BY (c.source = 'incident') DESC LIMIT 1) AS comment "
           f"  FROM runners r "
           f"  JOIN runner_tags t ON t.race_date = r.race_date "
           f"                    AND t.race_no = r.race_no "
           f"                    AND t.horse_no = r.horse_no "
           f" WHERE r.horse_name IN ({marks})")
    params: list[Any] = list(names)
    if before:
        sql += " AND r.race_date < ?"
        params.append(before)
    sql += " ORDER BY r.horse_name, r.race_date DESC, r.race_no DESC"

    # Which starts count as "recent" is decided per horse from its own record,
    # not by a date window: six starts is six starts whether they took a
    # season or a year.
    starts: dict[str, list[str]] = {}
    for r in conn.execute(
            f"SELECT horse_name, race_date, race_no FROM runners "
            f"WHERE horse_name IN ({marks})"
            + (" AND race_date < ?" if before else "")
            + " ORDER BY horse_name, race_date DESC, race_no DESC", params):
        seen = starts.setdefault(r["horse_name"], [])
        key = f"{r['race_date']}:{r['race_no']}"
        if key not in seen:
            seen.append(key)

    out: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(sql, params):
        if r["tag"] not in VET_TAGS:
            continue
        recent = starts.get(r["horse_name"], [])[:runs]
        key = f"{r['race_date']}:{r['race_no']}"
        if key not in recent:
            continue
        out.setdefault(r["horse_name"], []).append({
            "tag": r["tag"],
            "race_date": r["race_date"],
            "race_no": r["race_no"],
            "runs_ago": recent.index(key) + 1,
            "comment": r["comment"],
        })
    return out


def _comments(conn: Connection, date: str, race_no: int,
              horse_no: int) -> tuple[str | None, str | None]:
    """Returns (running_comment, incident_comment).

    Two accounts of the same race, kept apart. Corunning is HKJC's objective
    description of where the horse went; the incident report is the stewards'
    account of what went wrong. The form guide shows both.
    """
    rows = conn.execute(
        "SELECT source, comment_text FROM runner_comments "
        "WHERE race_date=? AND race_no=? AND horse_no=?",
        (date, race_no, horse_no)).fetchall()
    by_source = {r["source"]: r["comment_text"] for r in rows}
    return by_source.get("corunning"), by_source.get("incident")


def _column(row, name: str):
    """A column if the query selected it, else None.

    `_to_line` is fed by more than one SELECT and they do not all carry every
    column. Indexing a sqlite3.Row that lacks one raises, which would turn a
    missing column into a broken page rather than a missing number.
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _to_line(row, tags: tuple[str, ...] = (), lane_notes: tuple[str, ...] = (),
             comments: tuple[str | None, str | None] = (None, None)) -> RunnerLine:
    return RunnerLine(
        race_date=row["race_date"], race_no=row["race_no"],
        horse_no=row["horse_no"], horse_name=row["horse_name"],
        draw=row["draw"], jockey=row["jockey"], trainer=row["trainer"],
        actual_weight=row["actual_weight"], declared_weight=row["declared_weight"],
        gear=row["gear"],
        venue=row["venue"], course=row["course"], surface=row["surface"],
        going=row["going"], distance=row["distance"], race_class=row["race_class"],
        field_size=row["field_size"] or 0,
        place=row["place"], place_code=row["place_code"],
        dead_heat=bool(row["dead_heat"]),
        finish_time=row["finish_time"], lengths_behind=row["lengths_behind"],
        win_margin=_column(row, "win_margin"),
        running_positions=parse_running_positions(row["running_positions"]),
        section_times=parse_section_times(row["section_times"]),
        et_figure=row["et_figure"], et_len_vs_par=row["et_len_vs_par"],
        et_len_vs_race=row["et_len_vs_race"],
        et_sec_vs_par=row["et_sec_vs_par"], et_n_eff=row["et_n_eff"],
        et_confidence=row["et_confidence"],
        pace_style=row["pace_style"], early_dev=row["early_dev"],
        late_dev=row["late_dev"],
        sarr=row["sarr"], sarr_rank=row["sarr_rank"],
        tags=tags, lane_notes=lane_notes,
        running_comment=comments[0], incident_comment=comments[1],
        win_odds=row["win_odds"],
        place_odds=row["place_sp"],
        place_dividend=row["place_dividend"],
    )


def get_race(date: str, race_no: int, *, conn: Connection | None = None) -> RaceLine:
    """One race, with its runners as RunnerLine."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            _LINE_SQL + " WHERE r.race_date = ? AND r.race_no = ? "
                        " ORDER BY r.horse_no", (date, race_no)).fetchall()
        if not rows:
            return RaceLine(race_date=date, race_no=race_no)
        runners = tuple(
            _to_line(r, *_tags(conn, date, race_no, r["horse_no"]),
                     comments=_comments(conn, date, race_no, r["horse_no"]))
            for r in rows)
        head = rows[0]
        return RaceLine(
            race_date=date, race_no=race_no, venue=head["venue"],
            course=head["course"], surface=head["surface"], going=head["going"],
            distance=head["distance"], race_class=head["race_class"],
            # RaceLine has carried an off_time field all along and nothing
            # filled it, so the Race Day header had no time to show and put the
            # venue in the slot instead — a value the global chrome already has.
            off_time=head["off_time"],
            field_size=len(runners), runners=runners,
        )
    finally:
        if own:
            conn.close()


def get_meeting(date: str, *, conn: Connection | None = None) -> list[RaceLine]:
    """Every race on one date."""
    own = conn is None
    conn = conn or get_conn()
    try:
        nos = [r[0] for r in conn.execute(
            "SELECT DISTINCT race_no FROM races WHERE race_date=? ORDER BY race_no",
            (date,))]
        return [get_race(date, n, conn=conn) for n in nos]
    finally:
        if own:
            conn.close()


def get_horse_form(horse_name: str, *, limit: int = 6, before: str | None = None,
                   conn: Connection | None = None) -> list[RunnerLine]:
    """A horse's recent runs, most recent first.

    Keyed on horse_name, never horse_id: horse_id is 0% populated from July 2026
    and degrading from April, so a join on it silently returns no history.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = _LINE_SQL + " WHERE r.horse_name = ?"
        params: list[object] = [horse_name.strip().upper()]
        if before:
            sql += " AND r.race_date < ?"
            params.append(before)
        sql += " ORDER BY r.race_date DESC, r.race_no DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [
            _to_line(r, *_tags(conn, r["race_date"], r["race_no"], r["horse_no"]),
                     comments=_comments(conn, r["race_date"], r["race_no"], r["horse_no"]))
            for r in rows]
    finally:
        if own:
            conn.close()


def list_meetings(*, limit: int = 50,
                  conn: Connection | None = None) -> list[dict[str, object]]:
    """Recent meeting dates, most recent first, for the global selector."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT race_date, venue, count(*) AS races FROM races "
            "GROUP BY race_date, venue ORDER BY race_date DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"race_date": r["race_date"], "venue": r["venue"],
                 "races": r["races"]} for r in rows]
    finally:
        if own:
            conn.close()


def list_horses(*, limit: int = 400, query: str | None = None,
                conn: Connection | None = None) -> list[dict]:
    """The horse index behind the command palette.

    retain_discard.md §3.3: six items in the nav, everything else reachable by
    typing. A horse is one of the things worth typing, so it needs to be
    addressable without a page of its own.

    Ordered by most recent run, not alphabetically -- the horse you want is
    almost always one that has run lately, and 1,967 names sorted A-Z puts the
    answer nowhere near the top.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = """
            SELECT horse_name, count(*) runs, max(race_date) last_run
            FROM runners
            WHERE horse_name IS NOT NULL AND horse_name != ''
        """
        params: list = []
        if query:
            sql += " AND horse_name LIKE ?"
            params.append(f"%{query.strip().upper()}%")
        sql += " GROUP BY horse_name ORDER BY last_run DESC, runs DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        if own:
            conn.close()


def habitual_styles(horse_names: Sequence[str], *, before: str | None = None,
                    conn: Connection | None = None
                    ) -> dict[str, dict[str, Any]]:
    """How each of these horses RUNS, from its own record. One query for a card.

    Not the last run's style. `runner_pace.pace_style` is one value per horse
    per run and answers "where did it sit that day"; a card is asking "where
    does it sit", which is a property of the horse and needs the record behind
    it. Read off the last run alone, a Leader that was ridden quietly once
    reads as a Midfield for the race everybody is about to bet into.

    The rule is `derive.pace.habitual_style` — the same function SARR's profile
    uses, so the badge on the card is the style the model scored the horse
    with and the style the Speed Map draws its ladder from.

    Every cell carries its evidence: `n` runs counted, the whole `counts`
    tally, and `last` — the most recent classified run — so a page can say when
    the horse's last start disagreed with its habit rather than quietly
    averaging that away.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        names = [n.strip().upper() for n in horse_names if n]
        if not names:
            return {}
        marks = ",".join("?" * len(names))
        # Bounded by the names asked for, which the (horse_name, race_date)
        # index answers directly. A card is a dozen horses with a few dozen
        # runs each; reading the whole pace table to answer that would be a
        # full scan per race.
        sql = (f"SELECT r.horse_name, p.pace_style "
               f"  FROM runners r "
               f"  JOIN runner_pace p ON p.race_date = r.race_date "
               f"                    AND p.race_no = r.race_no "
               f"                    AND p.horse_no = r.horse_no "
               f" WHERE r.horse_name IN ({marks}) AND p.pace_style IS NOT NULL")
        params: list[Any] = list(names)
        if before:
            # STRICTLY before. Today's own run is the result, and a card that
            # read it would be scoring the horse on the race it is previewing.
            sql += " AND r.race_date < ?"
            params.append(before)
        sql += " ORDER BY r.horse_name, r.race_date DESC, r.race_no DESC"

        seen: dict[str, list[str]] = {}
        for row in conn.execute(sql, params):
            bucket = seen.setdefault(row["horse_name"], [])
            if len(bucket) < STYLE_WINDOW:
                bucket.append(row["pace_style"])

        out: dict[str, dict[str, Any]] = {}
        for name in names:
            styles = seen.get(name, [])
            counts: dict[str, int] = {}
            for st in styles:
                counts[st] = counts.get(st, 0) + 1
            out[name] = {
                # None where the record says nothing. A horse with no
                # classified run has no habitual style, and printing an
                # invented one in the same ink as a measured one is the bare
                # number this project keeps off the screen.
                "style": habitual_style(styles),
                "n": len(styles),
                "counts": counts,
                "last": styles[0] if styles else None,
                "window": STYLE_WINDOW,
            }
        return out
    finally:
        if own:
            conn.close()


def latest_appearance(name: str, *, conn: Connection | None = None) -> dict:
    """Where to send someone who just typed this horse's name.

    The command palette used to hand `?horse=NAME` to the Form Guide, which
    never read it -- so every horse landed on race 1 of the newest meeting and
    you went hunting. The destination is a question about the data, so it is
    answered here rather than guessed in JavaScript.

    Three answers, in the order that makes the newest thing win:

    1. DECLARED ON THE NEWEST CARD -- the race it is in. This beats a more
       recent trial on purpose: if the horse runs on Saturday, Saturday is what
       you opened the search for.
    2. OTHERWISE, WHICHEVER RAN LAST -- its last race or its last trial. A horse
       that raced on 1 July and trialled on 28 August is a horse whose news is
       the trial; one that raced last week is not. Comparing the dates is the
       only rule that gets both right, and "latest" is what was asked for.
    3. NOTHING -- 107 horses in the archive have trialled but never raced, and
       a name with neither says so rather than returning a page that will be
       empty when it loads.

    `race_no` comes back with the race so the Form Guide can open the right one
    rather than the first.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        horse = (name or "").strip().upper()
        out: dict = {"horse_name": horse, "kind": "none", "on_latest_card": False}
        if not horse:
            return out

        latest_card = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
        if latest_card:
            row = conn.execute(
                "SELECT race_no FROM runners WHERE horse_name = ? AND race_date = ? "
                "ORDER BY race_no LIMIT 1", (horse, latest_card)).fetchone()
            if row:
                return {**out, "kind": "race", "on_latest_card": True,
                        "race_date": latest_card, "race_no": row["race_no"]}

        run = conn.execute(
            "SELECT race_date, race_no FROM runners WHERE horse_name = ? "
            "ORDER BY race_date DESC, race_no DESC LIMIT 1", (horse,)).fetchone()
        trial = conn.execute(
            "SELECT trial_date, trial_no FROM trials WHERE horse_name = ? "
            "ORDER BY trial_date DESC, trial_no DESC LIMIT 1", (horse,)).fetchone()

        run_on = run["race_date"] if run else None
        trial_on = trial["trial_date"] if trial else None
        # A tie goes to the RACE. They cannot fall on the same day in practice
        # -- HKJC does not trial on a race day at the same track -- but a tie
        # resolved arbitrarily is a coin flip in the interface, and the race is
        # the more informative of the two.
        if run_on and (not trial_on or str(run_on) >= str(trial_on)):
            return {**out, "kind": "race", "race_date": run_on,
                    "race_no": run["race_no"]}
        if trial_on:
            return {**out, "kind": "trial", "trial_date": trial_on,
                    "trial_no": trial["trial_no"]}
        return out
    finally:
        if own:
            conn.close()
