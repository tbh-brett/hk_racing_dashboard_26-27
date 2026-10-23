# Race shape: the simulation on the Speed Map

What `claude/handover/racesim-SPEC.md` became, and how to run it. Brett's decisions (23 Oct 2026):
the simulation lives on the **Speed Map** page (not Race Day); the **2D replay of run races is on
hold**; the panel is built in the dashboard's own style (no artboard yet); **node is in the image**,
called from one job; the simulation runs **on the one Fly machine, one race per fifteen minutes**.

## What runs, and when

| When | Job | What it does | Cost |
|---|---|---|---|
| every landed meeting (inside `nightly`, via `derive_all.run_landed`) | `rebuild_trip` (the "trip" step) | the Comments on Running and stewards' report read into `runner_trip` and `race_trip` | ~100 runners, under a second |
| 04:10 daily, `--pending` | `fit_shape` | refits the four shape models once a meeting has settled, then rescores every stored card | 2–3 min of two threads; once a season ~twice that (its rounds and check models) |
| with `project_card` and `raceday_card` | `score_shape` | the shape models' predictions for a card; skipped when the card has not changed | ~30 s when it runs |
| every 15 min (:07 :22 :37 :52) | `sim_card --next` | ONE thing: a new card's tables (`fit_sim_tables`, ~10 s), or one race's simulation in node | a 14-runner race ~60 s of CPU |

A race is simulated again only when its fingerprint moves: the field or a rider, v2 or the shape
models by 0.01, new tables, a new engine. A race that has gone off is never simulated again: its last
row is its record. An eleven-race card is done in under three hours.

## First deploy, once

Run each at nice 19, at a quiet hour (`fly ssh console`, as docs/deploy.md describes):

```
nice -n 19 python -m hkrd.jobs.rebuild_trip --db /data/hkrd.db
nice -n 19 python -m hkrd.jobs.fit_shape --db /data/hkrd.db
nice -n 19 python -m hkrd.jobs.import_sim_record --file ops/records/sim_2026-10-19.json.gz --db /data/hkrd.db
```

`rebuild_trip` reads the whole archive (72,023 runs, about a minute here). The first `fit_shape` fits
the season's check models as well as the card models: about five minutes of two threads, more than
Fly's 500 s burst balance, so the machine will be throttled for a while after it. Pick a night with no
racing. The import is the lab's frozen simulation of 19 Oct, the record's first meeting; running it
twice changes nothing. From then on the crontab does the rest.

Check it with `python -m hkrd.jobs.sim_card --verify <date> <race>` (re-runs a stored race and fails
unless the bytes are identical), the `job_runs` rows for `sim_card` and `fit_shape`, and the Speed
Map's RACE SHAPE panel. Each tick records how much CPU Fly has withheld from the machine (`steal` in
`/proc/stat`; Fly's machines have no cgroup throttle counter) and runs nothing when more than a minute
was withheld since the last tick. Its reading is in each `sim_card` row of `job_runs`.

## What it reproduces (checked 23 Oct on the 23 Oct pull)

- The comment parser: equal to the lab's on all 103,766 comments and reports in the archive; the
  contacts equal on the 127 reference races.
- The vendored engine and the node runner: equal to the lab, byte for byte, on 25 Oct race 7 and on
  the whole 25 Oct card at lab settings; the engine checks (`test_core`, `marks_check`, traffic) equal.
- The simulator's tables, Part A3's costs and intervals, and the shape models' 25 Oct predictions
  (within 1e-16): equal on the lab's inputs (`HKRD_LAB_PARITY=1`, tests/test_shape_models.py).
- Refitted on the production parse: first-call position off by 2.168 places against the Speed Map's
  2.308 on 26/27 (1,465 runners, z 5.5).
- 19 Oct's record: P(lead) log loss 1.213 against 2.554 for a guess; first call missed by 18.7% of
  the field; win and top 3 tie v2. No skill yet on 3+ wide, no cover or held up (none clear of the
  season-before rate by twice its standard error).

## On the day: the scenario and today's races (built 24 Oct)

Under the race's table, SCENARIO runs the race again in the browser on the stored anchor, twice on
the same random numbers (neutral, then under the assumption), about 0.4 s for 2 x 20,000 races. Its
three levers are parameters the engine already has, so the vendored files are untouched: the early
pace (which borrowed clocks it may draw: fast, neutral or slow early), LEADERS (`early` in the cost
table: lengths for leading against racing last) and THE RAIL (`turn` 2, 3 and 4+: lengths for being
off the rail turning in). A scenario that has been run is what "Watch a run" plays. Only the 25 Oct
card on can use it; the 19 Oct record imported from the lab carries no borrowed clocks.

Above the panel, TODAY'S RACES lists each finished race's first three with their draw and first-call
position, and three buttons for where each came in the straight (`day_lanes`; a second click takes
one back, CLEAR removes a race). FETCH asks HKJC for races that have gone off (`market_close`) and
have no result; a page from another meeting, which HKJC serves for an unpublished race, is refused.
The day's read is a conditional logit on the winner with the price as the offset, shrunk towards no
bias, in lengths with its error; SET THE SCENARIO TO TODAY'S READ puts it on the sliders and runs
nothing.

The record it sits beside (`python -m hkrd.jobs.fit_day_bias`, ~40 s, stored in `day_bias_record`):
over 614 meetings since 2019 a meeting's first half has not predicted its second beyond the price
(leaders r -0.04, inside gates -0.04, the rail +0.05), where the same test finds days that differ by
about 1.8 lengths between leading and racing last; carrying the read into the next race made the
winner's log loss 0.0043 a race worse than the price alone (better in one season of eight). So the
sliders start at neutral and the read is shown as what happened.

## Not built (separate tickets, or on hold)

The 2D replay of run races and Brett's marking grid (`run_marks`, `race_reads`) — on hold. The
calibration items of the spec's §14, the Speed Map decision (§13.10: the settle model beats the
ladder, but replacing it is its own change), a Happy Valley course drawn from the map.
