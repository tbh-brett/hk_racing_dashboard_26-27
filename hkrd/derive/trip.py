"""The 2D trip of one run, and the contacts of one race (racesim-SPEC §3, §4).

One row per run (`runner_trip`): what the Comments on Running say about the run across the track
-- lane, cover, a slow start, traffic -- by phase, and the run from the home turn. Every model and
table fitted on "the trip" (model/sim_tables, model/shape) reads these columns, so they are the
Comments on Running alone, as the study's trip table was. `phases_json` is the replay's fuller
reading, the comment and the stewards' report merged (derive/lateral.merge).

One row per race (`race_trip`): the stewards' contacts placed on the course (derive/contacts).

A lane not stated stays None -- never the rail. HKJC's placeholder is no comment (§4.3): a run
whose only text is "No Comments on Running information for this horse." has `has_comment` 0 and
reads exactly like a run with no comment at all. Pure functions; jobs/rebuild_trip writes them.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from hkrd.derive import contacts as contacts_d
from hkrd.derive import lateral, runway

__all__ = ["DERIVE_VERSION", "COLUMNS", "positions", "run_row", "race_events"]

DERIVE_VERSION = lateral.DERIVE_VERSION

# The columns of `runner_trip` after the key, in table order.
COLUMNS = ("has_comment", "lane_settle", "lane_mid", "lane_turn", "lane_straight",
           "nocover_settle", "nocover_mid", "nocover_turn", "nocover_straight",
           "stated", "wide3_pre", "nocover_pre", "rail_pre", "lane_max_pre", "wide_straight",
           "slow_bad", "slow_mild", "traffic_pre", "traffic_straight",
           "run_way", "run_from", "phases_json", "derive_version")


def positions(running_positions: str | None) -> list[int]:
    """HKJC's running positions ("4 4 3 1") as integers; a "---" call is left out, as the replay does."""
    return [int(p) for p in (running_positions or "").split() if p.strip().isdigit()]


def _text(t: str | None) -> str | None:
    return None if not t or not t.strip() or lateral.is_placeholder(t) else t


def run_row(corunning: str | None, incident: str | None, *, distance: int, venue: str | None,
            surface: str | None, running_positions: str | None) -> dict:
    """One run's `runner_trip` columns from its Comments on Running and stewards' report."""
    lh = lateral.straight_length(venue, surface)
    cor, inc = _text(corunning), _text(incident)
    parsed = lateral.parse(cor, distance, lh)
    summary = lateral.summarise(parsed)
    phases = lateral.merge(lateral.rich(cor, distance, lh), lateral.rich(inc, distance, lh))
    run = runway.run_of(cor, distance, lh) if runway.runs_from_behind(positions(running_positions)) else None
    if run:
        phases["run"] = run
    row = {k: parsed[k] for k in ("lane_settle", "lane_mid", "lane_turn", "lane_straight",
                                  "nocover_settle", "nocover_mid", "nocover_turn", "nocover_straight",
                                  "stated", "slow_bad", "slow_mild", "traffic_pre", "traffic_straight")}
    row.update(summary)
    row.update(has_comment=int(cor is not None),
               run_way=run["how"] if run else None, run_from=run.get("from") if run else None,
               phases_json=json.dumps(phases, separators=(",", ":")),
               derive_version=DERIVE_VERSION)
    return {k: row[k] for k in COLUMNS}


def race_events(runners: Sequence[dict], *, distance: int, venue: str | None,
                surface: str | None) -> list[dict]:
    """The race's stewards' contacts placed on the course. `runners`: {no, name, inc} for every
    horse that ran; a placeholder report is no report."""
    field = [dict(no=r["no"], name=r["name"] or "", inc=_text(r.get("inc"))) for r in runners]
    return contacts_d.contacts(field, distance, lateral.straight_length(venue, surface))
