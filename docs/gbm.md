# The fundamental model (gbm) — what was built, what differs from the spec, and why

Phase 1 of `claude/handover/gbm-SPEC.md`, built on branch `gbm` from 29 Sep 2026. The spec
and the lab (`claude/tools/model-lab/`) are the reference; this file records only where the
build departs from them or had to decide something they left open.

## Where it lives

| Step | Code | Checked by |
|---|---|---|
| Inputs | `derive/features.py`, `store/gbm.load_runs`, `store/asof.cut_copy` | equal to the lab bit for bit (given its row order); §14.2 audit: 6 meetings, 691 runners, 85/85 |
| Model | `model/gbm.py`, `model/gbm_record.py` | the lab's predictions bit for bit on the lab's inputs |
| Fit | `jobs/fit_gbm.py` → `gbm_models` | §8 on the PC archive: pooled R² 0.1255 (0.126), every season within 0.0016 |
| Replay | `jobs/replay_gbm.py` | §14.2 on Fly's copy: 0.1417 / 0.1404 (0.145 / 0.143), top three 29 (29), top pick 14 (16) |
| Scoring | `jobs/score_gbm.py` → `runner_gbm`; hooks in `jobs/nightly`, `jobs/project_card` | tests/test_gbm.py |

Re-run the replay after any change to the inputs or the model:
`python -m hkrd.jobs.replay_gbm --db <a copy of Fly's database>`. Never run `fit_gbm` against a
database you want to keep unchanged: it writes `gbm_models`.

## Decisions and departures

**Tied prices rank in key order.** The lab read runners in storage order — finishing order for
a results-scraped race, saddlecloth order for a card-scraped one — and broke ties in the market
rank by it. A VACUUM or a delete-and-reinsert repair would move that. The build reads key order.
It changes the three `beat_mkt` inputs on about 5% of runs and costs 0.0007 R² on §8.

**The tree count moves.** Early stopping picks 355 trees on Fly's copy of 29 Sep where the lab
picked 412: the validation curve is flat from about 350 to 420, and the tie order alone moves it
from 408 to 355 on the same data. With 412 forced the replay gives 0.1439 / 0.1418. Kept as the
rule (early stopping), not a constant.

**The gate.** §5's calibration rule ("every band below 35% within ±3 points") failed an honest
out-of-sample model in 65% of 426 eight-meeting windows; "3 points and 2 standard errors" in 8%,
and neighbouring nights share seven of their eight meetings, so each failure would freeze the
model for a week. Calibration is a sanity check at 3 points AND 3 SE (0%). The protection is the
race-by-race log loss, one-sided at 10%, of the new recipe fitted before the last eight meetings
against what the page showed for them — or, until the page has shown eight, the live recipe
fitted the same way. A fit whose inputs or library differ from the live one and has nothing
shown to compare against waits for `fit_gbm --promote VERSION` by hand. (Brett, 29 Sep: "decide
for me".)

**Two scores a runner.** `runner_gbm.stage`: `card` is the first score, when the card lands —
written once, never rewritten, the night-before read on the record before any price. `latest` is
rewritten whenever the card's declared facts or the live model change, and stops when the race is
run, so at the off it is what the page showed. §3's primary key had one row a runner; §14.3 asked
to keep both.

**Card riders.** A card names an apprentice with the claim — "H Y Yuen (-7)" at 122 lb — where a
result names the rider and the weight carried, 115. The model learned the second, and the replay
could not see the difference because it rebuilt cards from results rows. Read the first way,
every apprentice on a card looked like a rider with no record: VOYAGE BOSS on 1 Oct read 19.3%
against the lab's 29.3%. `derive/features` now reads the card as a result would (23.3%; the rest
is the tree count and the July 2026 ratings hole).

**The Screen on the model** (§6). The Briefing's payload keeps its shape; its engine is
`runner_gbm`. For and against are the factor groups at ×1.05 / ×0.95 and beyond (smaller pushes
are bookkeeping), not the rider (its own line) or the race (the same for every runner). The
set-up verdict reads the groups that describe today rather than the horse -- draw, weight,
campaign, trip & track, pace & sectionals -- plus the rider against the field, at the old
thresholds. `leader_x`, the old engine's lone-leader multiplier, is gone: the model learns that
interaction itself, and the Briefing's lone-leader line now quotes the flag's measured record
("price A/E 1.17 over 1,594 runs: a lead, not an edge"). The "trialled well" line stays, said
as what the model cannot see. A card the model has not scored shows no chances rather than an
old engine's. `record_screen` records only meetings the scoring model was not trained on, stamped
with that model's version.

**Two definitions of a leader.** The Race Day flag "lone leader" is the spec's (§14.4): mean
first-call position in the front 15% over the last three runs, the only one in the field -- the
definition its five-season record measures. The Briefing's "only habitual leader" line reads the
dashboard's running style (derive/pace, field-scaled). The two usually agree; the Briefing quotes
the flag's record only where they do, and names no figure where they do not.

**Flags read stored facts.** `runner_gbm.facts_json` carries what the five flags read, so
`query/gbm` applies the rules (`model/gbm_record.FLAGS`) without rebuilding inputs in a request.

## Not done in phase 1

- **Lane notes arrive late** (§13.2). HKJC writes the comments on running 5–8 days after a
  meeting; until then a horse's last-run "wide" count reads low rather than unknown. The group
  is about 1% of the model's push.
- **The card's own rating change** (§13.2) is not stored by the card scraper, so the July 2026
  ratings hole leaves `rating_change` unknown for horses whose last run was in July.
- **§13.5 data fixes** (July ratings, missing classes, three stewards' phrases) are their own
  ticket.

## Cost

On this PC: inputs 5 s and 286 MB; a fit with the record built 32 s, carried 15 s; peak 365 MB
(the lab's walk-forward alone: 564 MB); a card 5 s, unchanged 0.01 s. The Fly machine had
~590 MB free at idle with no swap; Brett approved `shared-cpu-2x` with 4 GB (§13.1) on 29 Sep.

## The research copy (§14.9)

Fly's database is the research copy. `.\ops\pull-db.ps1` pulls it to `hkrd-fly.db`
(git-ignored) and keeps the previous pull as `hkrd-fly.prev.db`: SQLite's online backup at
nice 19 into `/tmp` on the machine -- a reader, consistent at one moment -- then the download,
then the copy deleted. Nothing is ever sent up. A Litestream restore would do the same without
touching the machine, but needs the R2 keys on this PC; say so if that is wanted.

Checked 29 Sep 2026 against HKJC's own list of trial days (`ingest/trials.list_days`): every
trial day since 2 September is on Fly -- 16 days, 3-29 Sep; 29 Sep arrived with the 20:00
scrape. The PC's `hkrd.db` stops at 2 September for trials and 9 September for results.

## What the model cannot see, beside it (30 Sep 2026)

Brett asked whether the old Screen's qualitative side -- trials, vet findings, a new stable,
the stewards' tags -- should come back now the model is the engine. Tested on Fly's copy of
29 Sep over 3,365 races, 2022-23 to 26/27, every season scored by engines that had never
seen it:

| | old Screen (screen-1.0) | model | closing price |
|---|---|---|---|
| R² | 0.112 | 0.129 | 0.194 |
| top pick won | 24.8% | 26.3% | 30.9% |
| winner in the top 4 | 62.4% | 63.6% | 70.9% |

- **Standalone** the model is ahead in every season.
- **Mixed** into one number (a stacked logit, weights fitted on earlier seasons) the old
  Screen adds nothing: +0.0006 a race, se 0.0036. The model plus the old Screen's named facts
  as an offset: the same.
- **Side by side** they share a top pick 61% of the time, and where they differ the model's
  won 20.3% against 16.6%. But the horses in the Screen's four and not the model's won 1.22x
  what the model gave -- and that is a handful of facts, not the Screen as a whole. Against the
  model: a STANDOUT trial since the last run 2.0x, POSITIVE 1.31x, NEGATIVE 0.63x (2025-26
  on, the only trials the archive holds); a new stable 1.36x; second-up after a bad first-up
  0.73x and the lone leader 1.20x (already the model's flags). Everything else -- first-up,
  debut, beaten, wide, an excuse, draw moves, rating moves, class, first-time gear, the card's
  vet notes -- came out at about 1.00: the model carries it. Against the price, all of them sit
  near 1.00.
- Trials on held-out meetings (2025-26 split into alternate meetings): +0.0087 a race, t 1.5 --
  promising, not proven, and flattered: `derive/trial_quality`'s phrases were chosen on the
  same season's results.

So the facts are said beside the number and never added to it. `model/gbm_unseen` defines
them (a trial since the last run within 60 days, the old Screen's window; a different trainer
from the last start), `jobs/fit_gbm` measures each against the walk-forward chances into the
record's `unseen` table, and the Briefing shows them under NOT IN THE MODEL with that figure,
its range and the price's -- or "not measured yet" until a record holds the table. A record
built before `unseen` existed is rebuilt at the next fit, not carried. The model's flags join
the same block (the Briefing never drew them before), and a good trial is a Briefing reason
at any rank, because the rank cannot see it. Trials go into the model itself only when the
archive holds two seasons of them.

The VET and NEW TR chips had stopped appearing when the model replaced the Screen: they were
keyed on the old factor names. They read the Screen's own `vet` and `unseen` fields now.

## Speed (30 Sep 2026)

Measured on the machine after v50: the Briefing's API 220-300 ms, 95% of it rebuilding the
Screen on every read (every minute on race day); Race Day ~45 ms a race; the Model Analysis
backtest ~1 s. CPU steal over the first 42 minutes: 1.6 s -- Fly was not throttling. From
Brett's PC a round trip to Singapore is ~45 ms, and the Briefing waited on seven in series.

- `query/screen.meeting` keeps the finished Screen per database and date until a fingerprint
  of what it reads changes (the card, its results and scores, the model, the history's new
  rows, trials, notes, the blackbook), and ten minutes at most. The fingerprint costs ~2 ms.
- `query/gbm.scores` reads plain rows (the pandas row loops were 40% of the Screen), and the
  live model's record is parsed once per model.
- A trial's rating is remembered (`query/trials.rate`): the same comments were matched against
  the same phrases on every read.
- Every page names its modules up front (`<link rel="modulepreload">`, kept true by
  `tests/test_web_preload.py`); the page context asks for the named date's card beside the
  meeting list; the Briefing asks for its own data at once and for its two records together.
- Model Analysis opens on MODEL and reads each other view the first time it is shown.

On this PC, same data: the Screen 121 ms to 2 ms when nothing has changed (~100 ms when it has),
the Briefing 125 ms to 6 ms; the Briefing's load went from seven waves of requests to three.
