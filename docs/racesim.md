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

## The all-weather (26 Oct, `simtables-1.1`)

Measured on the archive since 2019 (332 AWT races at 1200, 219 at 1650, 49 at 1800):

- Margins are wider than turf's: the winner wins by 2 L or more in 22-28% of AWT races against
  12-17% on turf, the last horse finishes a median 14.5-19 L behind against 9-14, and the spread of
  finishes once the price is known is 4.9-5.7 L against 3.2-4.7. The tables kept one spread for
  every trip (4.0 L), which drew AWT margins as turf's and let the trip decide too much of an AWT
  result. `noise.by_group` is now the same fit on each trip group's runs, and
  `sim_tables.for_race` puts the race's own spread where the engine reads its one number, for a
  run, its `--verify` and the page alike, so the vendored engine is unchanged.
- Leaders who led early beat their price on the AWT (1.38x at 1200, 1.64x at 1650) as they do on
  turf (1.45x at Sha Tin 1200, 1.40x over 1400-1800); closers do worse than their price (0.57-0.71x)
  as on turf sprints. Known before the race, by habitual early speed: about the price at 1200, 1.20x
  at 1650. The AWT cost tables already carry this: at 1200 the early pace against position costs
  4.7 L a unit against turf's 2.25, and at 1650 settling four or more wide costs 3.4 L.
- The draw: inside gates win more (1650: gates 1-3 10.4%, 10-14 4.3%), but against the price the
  inside is not distinguishable from zero. The engine plays it through where a horse settles.
- A night's bias: on the 32 nights with six or more AWT races, the first AWT races did not predict
  the later ones (leaders r +0.11) and carrying the read forward was no better than the price
  (log loss +0.0013 +/- 0.011 a race, better in two seasons of seven); 32 nights reliably
  show only a night effect of about 8 L, so this rules out little.

TODAY'S RACES reads a card's AWT and turf races apart, each against its own record and in its own
races' spread of lengths, and only sets an AWT read on an AWT race. An AWT race's Race Shape panel
has one line of what the archive says of its trip (`trips` in the day-bias record). The scenario
sliders reach further where results spread more (4 L at AWT 1650, 3.5 at AWT 1200, 3 on turf).

## Happy Valley's own course (29 Oct, `simtables-1.2`)

Until 29 Oct the engine drew and ran Happy Valley as Sha Tin's shape made smaller: two 310 m straights
and two equal 415 m bends of 132 m. The course is now HKJC's map, traced from the turf's inside rail,
checked against HKJC's own start markers (the 1650 and 1800 on the home straight, the 1200 at the
chute, the 2400 under its arrow), scaled to the A course's 1,450 m lap and fitted as straights and
arcs (2.8 m from the traced rail on average). In metres to go on the last lap: the home straight
0-310; the home turn 310-630, 162 degrees in three arcs (entry 149 m, middle 83 m, exit 116 m); the
back straight 630-938; a sharp top-left corner 938-983 (61 m); the gentle left side 983-1193 (269
m); the bend after the post 1193-1420 (117 m), which starts 30 m past the post. Brett confirmed the
chutes: the 1000 (and the 2400) start straight onto the back straight, 62 m before it; the 1200 in
the chute at the bottom left, 50 m, then up the left side. Ground lost is each bend's own radius;
the replay draws the course from the same parts.

From the archive (2019 on, Comments on Running):

- A horse making three or more places into the turn comes round 0.7-0.8 of a lane wider than the
  field at HV 1650+, against a twentieth of a lane at Sha Tin and 0.2 in the HV sprints. The engine
  gave 0.2-0.35 everywhere, so a horse going round on the HV home bend at 1650 m and beyond is now
  carried 1.5 lanes outside where it entered it (`DRIFT`); measured on the 1 Nov 1650 and 1800, those
  movers turn in 0.36 of a lane wider than the field, from 0.21. Going wide there also costs little
  against the price (4+ wide at HV 1650+ A/E 1.05, Sha Tin 0.49).
- Rail runners in the front 60% turning in are held up in the straight 5-6% of the time at HV,
  10-13% at Sha Tin; midfield rail runners 7% against 15-18%. `traffic.by_venue` gives each venue
  its own rates (HV base 5.3%, Sha Tin turf 8.7%, the AWT 3.3%; pooled 6.9%).
- The comments almost never say where a leader went in the straight, so when a gap opens on the HV
  rail is for Brett's marked races, not the archive.

Uphill out of the bend after the post and downhill into the home turn are in the borrowed clocks,
which are HV races' own sectionals. Sha Tin and the AWT are the lab's to the byte: the engine's 64
Sha Tin replays are identical to the lab engine's (tests/test_shape_engine.py).

## Not built (separate tickets, or on hold)

The 2D replay of run races and Brett's marking grid (`run_marks`, `race_reads`) — on hold. The
calibration items of the spec's §14, the Speed Map decision (§13.10: the settle model beats the
ladder, but replacing it is its own change).
