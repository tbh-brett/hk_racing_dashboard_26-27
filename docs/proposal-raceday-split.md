# Proposal: where the "why is this runner unrated" answer should live

**2026-09-15.** `docs/handover.md` §4.4 asks for a split to be proposed before
anything moves. This is that proposal.

> **DECIDED 2026-09-15: all three were built, in that order.** A and C first,
> then B in its own commit with its own before/after check. This file stays as
> the record of what each was and what the audit found; `docs/decisions.md` has
> the outcomes.
>
> **Two things below turned out to be wrong, and are corrected in place.** The
> audit said four readers take row existence for a rating; there are **six**.
> And the table promises "copies of the count: 1", which B did **not** deliver —
> see the corrections under Option B.

§4.3 remains a separate decision and is untouched.

---

## The thing that is actually blocked

Model Analysis names the runners the blend could not rate and deliberately does
not say why, because a blank is two different things:

- **too little history** — fewer than `sarr.MIN_PRIOR` prior runs, which is a
  rule and will happen on 65.2% of cards
- **a card nobody scored** — which is a fault, and was the live state of every
  upcoming card until `3b67054`

Only Race Day tells them apart, in `raceday._unrated`. To say the same thing on
Model Analysis, two things have to be reachable from both pages:

| | where it is now | size |
|---|---|---|
| the rule | `query/raceday.py:143` `_unrated(rank, prior)` | 17 lines, pure, depends only on `sarr.MIN_PRIOR` |
| the count | `query/raceday.py:264` inline SQL in `build_card` | 11 lines |

`query/raceday.py` is **568 of a 600-line cap**, so neither can grow there, and
`query/model.py` importing `query/raceday` would be one page's module reaching
into another's.

## What the audit turned up that the handover did not say

**The prior-run count is already computed in three places, under two different
definitions.**

| | predicate |
|---|---|
| `jobs/rebuild_sarr.py:252` | `(race_date, race_no) < (today, this_race)` — an earlier race on the SAME day counts |
| `jobs/project_card.py` (`HIST_SQL`) | `finish_time IS NOT NULL AND race_date < today` |
| `query/raceday.py:270` | `finish_time IS NOT NULL AND race_date < today` |

The comment on the third says it counts "the way the rebuild counts it". It does
not, quite. No horse runs twice on one card, so the two agree on every row in
the archive today — but they are two rules, and only one of them is the one the
score was built from.

**An unrated runner has no `runner_sarr` row at all.** `rebuild_sarr` writes only
what it scored (`continue` on `len(prior) < min_prior`), which is why Race Day
re-counts history at request time instead of reading `n_prior` off a join. It is
also why `runner_sarr` holds 17,262 rows against 21,280 runners.

**`sarr`, `sarr_rank` and `n_prior` are already nullable.** Whatever is chosen,
no schema migration is involved.

---

## Option A — a new `query/rating.py`

Move `_unrated` and the count into a small module both pages import. It owns one
question: *what does the model know about this runner, and if nothing, why.*

```
query/rating.py      unrated_reason(rank, prior)      the rule, unchanged
                     prior_run_counts(conn, names, before)   one grouped query
```

- `query/raceday.py` → **~540 lines**, and the inline SQL leaves `build_card`
- `query/model.py` adds the count to its existing query and the reason to its
  footer — no new round trip, the LEFT JOIN is already there
- three definitions of the count become two: the page-side one and the job-side
  one, which stay separate because the jobs work over a pandas frame of the whole
  archive rather than per-card

**Cost:** one new file, ~45 lines. Nothing existing changes behaviour.
**Leaves behind:** `raceday.py` still at 540, above the 500-line "propose a split
before adding" line. And the jobs still carry their own count.

## Option B — `runner_sarr` gets a row for every runner

`rebuild_sarr` writes a row for runners it did not score too, with `sarr` and
`sarr_rank` NULL and `n_prior` filled. The count then arrives on a join that four
modules already do, `raceday`'s request-time query disappears entirely, and the
rule becomes a pure function over columns that are already on the row.

This is the central rule applied properly: the number is computed once, by the
job that owns the definition, and read everywhere.

**But it changes what "there is a `runner_sarr` row" means, and ~~four~~ SIX
places read it that way.** The audit found four by grepping for `runner_sarr`.
Two more only showed up when the change was made, and both are the kind that
fail silently:

| | what breaks |
|---|---|
| `model/power.py:150` | inner `JOIN runner_sarr` — the rated-population count silently widens to the whole field |
| `query/model.py:171` | `sarr_breakdown` inner-joins and does `ORDER BY s.sarr_rank`; NULL sorts first in SQLite, so unrated runners head the SARR panel |
| `query/model.py:139` | the freshness strip prints `runner_sarr` row counts; 17,262 → ~21,280 overnight, and "is it complete" becomes a different question |
| `jobs/rebuild_sarr.py` | `skipped_no_history` stops meaning skipped |
| **`model/evaluate.score`** | **missed by the grep — it reads `score_runners`' return, not the table. A NaN score would widen the population every variant is measured over, in the one place built to detect exactly that kind of drift** |
| **`jobs/derive_all.py:132`** | **sums the two skip counters into the freshness strip's "not produced" figure** |

Each table fix is one clause, and each omission is silent — which is the
failure mode this rebuild exists to remove. `tests/test_sarr.py` now fails if
any inner join on the table is added without the guard.

**CORRECTION — B does not collapse the count to one definition.** The table
above predicted "copies of the count left: 1". It is still 2, for two reasons
found while building it:

- The fault this whole thread is about — *a card nobody scored* — is defined by
  the job's output being ABSENT. Where there are no rows there is no stored
  `n_prior`, so the page-side count cannot be retired; it is needed in exactly
  the case it exists for.
- `rebuild_sarr` counts to `(race_date, race_no) < (today, this_race)` and the
  pages to `race_date < today`. Reading the stored count on one page and the
  computed one on another would mix two rules rather than remove one, and
  reconciling them changes who the model scores — still a model change, still
  needing its own walk-forward check.

**What B did deliver** is the distinction the table could not previously make:
a race with no rows at all now means "nothing scored this card", where before it
also meant "the card was scored and nothing in it could be rated" — ordinary for
a maiden field of first-starters. `rating.race_was_scored` reads it, and a
debutant on an unscored card no longer reads DEBUT, which told the reader
nothing was wrong.

**Cost:** four guarded reads, a changed job report, and a full
`rebuild_sarr` — which production needs anyway under §4.1, so the expensive part
is already on the schedule.
**Risk:** the four above are the ones a grep for `runner_sarr` finds. A fifth
that joins without filtering would be wrong and would not raise.

## Option C — carve the meeting-level half out of `raceday.py`

Independent of the other two: `meeting_blackbook` and `meeting_summary` (lines
386–478, 93 lines) answer meeting-wide questions and touch nothing `build_card`
uses. Moved to `query/meeting.py`, `raceday.py` drops to **~475**, under the 500
line at last.

The tail below them does NOT move: `_days_between`, `_place_ratio_range`,
`_pairs_meeting_again` and `_swing_favours` are all called from `build_card`.
The seam is narrower than the file's shape suggests.

**Cost:** two `api/app.py` call sites change module. No behaviour change.
**It does not, on its own, unblock anything** — the shared count still has
nowhere to live.

---

## Recommended at the time

**A now, C alongside it, B not yet.** All three were built; B followed in its
own commit, which is what this section asked for.

A is the smallest change that actually unblocks the footer, and it is correct on
its own terms: the rating status of a runner is a property of the rating, not of
the Race Day page, so it belongs in neither page's module. C is free, is a real
seam rather than a line-count trim, and is the only one of the three that gets
`raceday.py` under 500 — do it in the same pass while the file is open.

B is the right end state and the wrong thing to ride along with a footer fix. It
is four silent-failure sites wide, and it wants its own commit, its own audit and
a walk-forward check that the rated population did not move — on a day when
nobody is waiting on it. Worth revisiting the next time `runner_sarr` needs a
rebuild for another reason.

One thing to fix in whichever is taken: the count's predicate should be stated
once and named, so `rebuild_sarr`'s tuple comparison and the page's date
comparison stop being two rules that happen to agree.
