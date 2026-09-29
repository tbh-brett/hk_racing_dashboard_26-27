"""Score a card with the live fundamental model: every declared runner's chance.

    python -m hkrd.jobs.score_gbm --date 2026-10-01
    python -m hkrd.jobs.score_gbm --pending          # every card still to run

WHEN (gbm-SPEC §14.3). When the card lands -- the night-before read, the
product -- and again whenever what HKJC has declared changes: a scratching, a
change of rider, the body weights and the going on race morning. Called by
`nightly` and `project_card` on every run; the card's declared facts and the
live model are fingerprinted first (`store/gbm.card_facts`), so a card that
has not changed costs one query and builds nothing. A race that has been run
is never scored again: its 'latest' row is what the page showed at the off.

WHAT IS ASSUMED, AND SAID (§13.2). A card published days ahead has no body
weights and no going. Body weight: the last run's (`derive/features`), and for
a horse with no run to carry one from, the median debutant's
(`model/gbm.assume_body_weight`). Going: Good. Each runner's `facts_json`
records which, so the page can say "body weight: last run's".

Not yet: HKJC's lane notes arrive days after a run, and until they do a horse's
last-run "wide" count reads low rather than unknown (§13.2 asks for unknown;
the group is about 1% of the model's push). The card's own rating change,
which would fill the July 2026 hole in the ratings, is not stored by the card
scraper. Both are noted in docs/gbm.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from hkrd.derive import features
from hkrd.model import gbm
from hkrd.store import gbm as store
from hkrd.store import job_log
from hkrd.store.connect import Connection, get_conn, init_db, transaction

__all__ = ["score", "score_pending", "main", "ScoreReport"]


@dataclass
class ScoreReport:
    scored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  scored     {s}" for s in self.scored]
        lines += [f"  unchanged  {d}" for d in self.unchanged]
        lines += [f"  note       {n}" for n in self.notes]
        lines += [f"  ERROR      {e}" for e in self.errors]
        return "\n".join(lines) or "  no card waiting to be scored"


def _key(facts: list[dict], version: str) -> str:
    blob = json.dumps([version, features.DERIVE_VERSION, facts], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


_FACTS = ("prep_run", "n_prior", "l1_place", "vs", "l1_vs", "l2_vs", "l3_vs", "hab_early",
          "n_leaders")


def _plain(v: object) -> object:
    """A frame value as JSON: numbers as numbers, missing as null."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    return v.item() if hasattr(v, "item") else v


def _score(conn: Connection, date: str, report: ScoreReport) -> None:
    live = store.live_model(conn)
    if live is None:
        report.notes.append(f"{date}: no model is live yet -- run jobs.fit_gbm")
        return
    facts = store.card_facts(conn, date)
    if not facts:
        return
    key = _key(facts, live["version"])
    if store.scored_keys(conn, date) == {key}:
        report.unchanged.append(date)
        return
    declared = {(f["race_no"], f["horse_no"]): f["declared_weight"] is not None for f in facts}
    going = {f["race_no"]: f["going"] is not None for f in facts}
    frame = features.build(store.load_runs(conn, through=date), card_dates=(date,))
    # a body weight as declared or carried from the last run, before the median fills the rest
    had_weight = frame["declared_weight"].notna().to_numpy()
    gbm.assume_body_weight(frame)
    keep = frame["is_card"].to_numpy() & np.array(
        [(r, h) in declared for r, h in zip(frame["race_no"], frame["horse_no"])])
    card, had_weight = frame.loc[keep].reset_index(drop=True), had_weight[keep]
    if len(card) != len(facts):
        raise ValueError(f"{len(facts)} runners declared but {len(card)} built")

    booster = gbm.from_text(live["model_text"])
    p = gbm.predict(booster, card)
    place = gbm.place_chances(p, card["race_id"].to_numpy())
    contrib = gbm.contributions(booster, card).round(4)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rows = []
    for i, r in card.iterrows():
        k = (r["race_no"], r["horse_no"])
        weight = "declared" if declared[k] else "last run" if had_weight[i] else "median"
        rows.append({
            "race_date": date, "race_no": r["race_no"], "horse_no": r["horse_no"],
            "p_win": p[i], "p_place": place[i], "contrib": contrib.iloc[i].to_dict(),
            "facts": {**{c: _plain(r[c]) for c in _FACTS}, "body_weight": weight,
                      "going": "declared" if going[r["race_no"]] else "assumed"},
            "model_version": live["version"], "derive_version": features.DERIVE_VERSION,
            "inputs_key": key, "scored_at": now})
    with transaction(conn):
        wrote = store.write_scores(conn, rows)
    n_decl, n_had = sum(declared.values()), int(had_weight.sum())
    report.scored.append(
        f"{date}: {len(rows)} runners in {card['race_no'].nunique()} races with "
        f"{live['version']} ({wrote['card']} first reads kept); body weight {n_decl} "
        f"declared, {n_had - n_decl} last run's, {len(rows) - n_had} median"
        + ("" if all(going.values()) else "; going assumed Good"))


def score(date: str, db: Path | None = None) -> ScoreReport:
    """Score one meeting's unrun races, if anything it depends on has changed."""
    report = ScoreReport()
    conn = get_conn(db)
    try:
        init_db(conn)
        _score(conn, date, report)
    except Exception as exc:                   # noqa: BLE001 - recorded, per date
        report.errors.append(f"{date}: {type(exc).__name__}: {exc}")
    finally:
        conn.close()
    return report


def score_pending(db: Path | None = None, *, today: str | None = None) -> ScoreReport:
    """Every meeting from today on with a race still to run."""
    report = ScoreReport()
    conn = get_conn(db)
    try:
        init_db(conn)
        dates = store.unrun_dates(conn, today=today or dt.date.today().isoformat())
    finally:
        conn.close()
    for date in dates:
        one = score(date, db)
        for k in ("scored", "unchanged", "notes", "errors"):
            getattr(report, k).extend(getattr(one, k))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--date", help="YYYY-MM-DD")
    which.add_argument("--pending", action="store_true", help="every card still to run")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    with job_log.running("score_gbm", a.db) as outcome:
        report = score_pending(a.db) if a.pending else score(a.date, a.db)
        outcome["ok"] = not report.errors
        outcome["detail"] = "; ".join(report.scored + report.errors) or "nothing changed"
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
