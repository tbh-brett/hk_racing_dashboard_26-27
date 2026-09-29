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
