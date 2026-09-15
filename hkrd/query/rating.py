"""What the model knows about a runner, and when it knows nothing, why.

A blank SARR rank is two different things and the difference matters more than
the blank does. SOLID STATE on 2026-09-13 had one run, which is a RULE: SARR
rates nothing with fewer than `sarr.MIN_PRIOR` prior runs, every card carries
debutants, and 65.2% of the archive's races hold at least one runner it will
not score. A horse with twenty runs and no rank is a FAULT: a card nobody
scored, which is what the nightly job left every upcoming card in until
`3b67054`. Rendered as the same dash, the fault passes for a debutant and
nobody looks.

WHY THIS IS NOT IN EITHER PAGE'S MODULE. Race Day answered this first and
answered it well, so the rule lived in `query/raceday.py`. Model Analysis then
needed the same answer for its blend footer, and `query/model.py` importing
`query/raceday` would be one page reaching into another's module for a fact
that belongs to neither. The rating status of a runner is a property of the
rating. It lives beside the thing that decides it, and both pages read it here.
"""
from __future__ import annotations

from typing import Any

from hkrd.model import sarr
from hkrd.store.connect import Connection

__all__ = ["unrated_reason", "prior_run_counts", "race_was_scored",
           "PRIOR_RUN_RULE"]

# The page-side definition of "a prior run", stated once so the two readers of
# it cannot drift apart: a run on an EARLIER DATE that has a finishing time.
#
# `jobs/rebuild_sarr` counts to a different rule -- `(race_date, race_no) <
# (today, this_race)`, under which an earlier race on the SAME day counts. No
# horse runs twice on one card, so the two agree on every row in the archive,
# and they are still two rules. Reconciling them changes who the model scores,
# which is a model change and needs a walk-forward check of its own; it is not
# something to fold into a page fix. Until then this is the rule the pages
# quote, and `jobs/project_card` already counts to it.
PRIOR_RUN_RULE = "runs on an earlier date that have a finishing time"


def unrated_reason(rank: int | None, prior: int, *,
                   card_scored: bool = True) -> dict[str, Any] | None:
    """Why a runner has no SARR rank, or None when it has one.

    `kind` is the part a page acts on: `history` is a rule and needs no
    attention, `unscored` and `no_card_score` are faults and do. `prior` and
    `needs` travel with it so no caller has to know the threshold to render
    the sentence.

    `card_scored` is the race-level question and it outranks the runner-level
    one. A card nobody scored whose field happens to be short of history would
    otherwise report every runner as a debutant -- true of the horse, and the
    wrong thing to tell someone who could fix it by scoring the card.
    """
    if rank is not None:
        return None
    if not card_scored:
        return {"kind": "no_card_score", "prior": prior,
                "needs": sarr.MIN_PRIOR, "label": "CARD NOT SCORED"}
    if prior < sarr.MIN_PRIOR:
        return {"kind": "history", "prior": prior, "needs": sarr.MIN_PRIOR,
                "label": "DEBUT" if prior == 0 else f"{prior} RUN"}
    return {"kind": "unscored", "prior": prior, "needs": sarr.MIN_PRIOR,
            "label": "NOT SCORED"}


def prior_run_counts(conn: Connection, names: list[str], *,
                     before: str) -> dict[str, int]:
    """How much history each of these horses brings to a card on `before`.

    One grouped query for the whole field, against `ix_runners_horse` -- per
    runner it is a scan apiece, and a per-race constant computed per row is the
    performance rule this project already has.

    A horse absent from the result has no qualifying runs; callers read it with
    `.get(name, 0)` rather than expecting a key, because a debutant is exactly
    the case that produces no row and exactly the case this exists to name.
    """
    names = [n for n in names if n]
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    return {row[0]: row[1] for row in conn.execute(
        f"SELECT horse_name, count(*) FROM runners "
        f"WHERE horse_name IN ({marks}) AND race_date < ? "
        f"AND finish_time IS NOT NULL GROUP BY horse_name",
        [*names, before])}


def race_was_scored(conn: Connection, date: str, race_no: int) -> bool:
    """Whether the rebuild has looked at this race at all.

    ONLY ANSWERABLE BECAUSE THE JOB RECORDS ITS REFUSALS. While `runner_sarr`
    held rows for rated runners only, a race with no rows meant either "nobody
    scored this card" or "the card was scored and nothing in it could be
    rated", and the second is ordinary: a maiden field of first-starters
    produces exactly that. One row per runner looked at makes the absence mean
    one thing again.
    """
    return conn.execute(
        "SELECT 1 FROM runner_sarr WHERE race_date = ? AND race_no = ? LIMIT 1",
        (date, race_no)).fetchone() is not None
