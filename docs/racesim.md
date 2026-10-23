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
Map's RACE SHAPE panel. The tick reads `nr_throttled` from `/sys/fs/cgroup/cpu.stat`; if the machine
has no such counter the tick says "not checked" and carries on — worth looking at once after deploy.

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

## Not built (separate tickets, or on hold)

The 2D replay of run races and Brett's marking grid (`run_marks`, `race_reads`) — on hold. The
calibration items of the spec's §14, the Speed Map decision (§13.10: the settle model beats the
ladder, but replacing it is its own change), a Happy Valley course drawn from the map.
