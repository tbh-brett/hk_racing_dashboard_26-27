# Proposal: the Blackbook as a tracker of conditions, not a list of names

**2026-09-16.** Asked for directly: *"the current iteration of black book (while
useful and practical) seems to be outdated and does not match with the dashboard
completely — it currently really only functions as a reminder module for myself
when manually screening through races."*

This proposes what it should become. Nothing here is built. The
expiry/retire fix shipped separately (`docs/decisions.md`, "an entry ends when
it is retired") and is assumed below.

The artboard — `web/design-source/Blackbook.dc.html` — stays the foundation, as
asked. Everything proposed is an addition to its grammar rather than a
replacement of it: the same row, the same tag chips, the same BY TAG table with
A/E and a 95% interval beside every rate.

---

## 1. Why it reads as a reminder module

Because that is structurally what it is. Four findings, each checkable:

### 1.1 The conditions a thesis depends on are stored and never read

`blackbook` has carried `pref_distance`, `pref_surface` and `pref_jockey` since
the legacy import. **Nothing reads them.** The only lines in the codebase that
mention those columns are the two in `jobs/import_blackbook.py` that write them:

```
$ grep -rn "pref_distance|pref_surface|pref_jockey" --include=*.py --include=*.js .
./hkrd/jobs/import_blackbook.py:208 …
./hkrd/jobs/import_blackbook.py:209 …
```

So an entry can say "this horse wants 1200m on dirt with a strong rider" and no
page, query or band ever asks whether today's race is that race. You are the
thing that checks. That is the reminder module, exactly.

### 1.2 The book and the archive speak different tag languages

`derive/tags.py` holds ~30 tags derived from frequency analysis over **10,852
real incident texts across 87 meetings** — `short_of_room`, `without_cover`,
`awkwardly_away`, `checked`, `bled`, `raced_keenly`. Every run in the archive
carries them.

The blackbook's 19 tags are hand-typed and separate: `traffic`, `improvement`,
`trial`, `gear_change`. A horse booked for `traffic` and a run tagged
`short_of_room` are **the same claim in two vocabularies that never meet**.

The cost is in `blackbook.tag_performance`. It measures a tag only over the runs
of horses carrying that tag — so "does trip trouble pay?" is answered on the
handful of runs in the book, is marked `thin` (correctly), and stays thin
forever. The archive can answer the same question on thousands of runs and is
never asked. The BY TAG table's own footer says the quiet part: *"a tag that
looks like it's working on 6 entries probably isn't."*

### 1.3 The thesis is prose, so nothing can score it

`reasoning` is free text. "Blocked at the 300" cannot be checked by anything.
The entry therefore has no testable content: `runs_since` and `record_since`
measure whether the horse *won*, which is not the same as whether the *claim*
was right. A horse booked because it was blocked can run a much better race and
finish fourth, and the book records that as a failure.

### 1.4 It does not know what the rest of the dashboard knows

The Blackbook joins to `runners` and to `bets`. It does not touch `runner_et`,
`runner_pace`, `blend`, `trials`, `runner_gear` or the market. So:

- no entry knows whether the model agrees with it
- no entry knows whether the horse has since improved on the figures
- `slices.outliers` explicitly says its rows are *"worth watching and worth a
  blackbook note"* — and there is no way to make one from there
- `trials.standouts` is the live feed of well-rated recent trials, and three of
  the artboard's ten tags are trial tags, yet no analysis separates
  trial-sourced entries from race-sourced ones

---

## 2. What to change

### 2.1 Make the thesis a CONDITION, not a sentence

The one structural change everything else follows from. An entry keeps its
prose and gains a small, typed **trigger**: the circumstances under which this
horse is interesting.

```
blackbook_trigger
  id            TEXT     -- the entry
  kind          TEXT     -- distance | surface | going | course | class |
                         -- draw | jockey | trainer | field_size | days_since
  op            TEXT     -- is | in | <= | >= | between
  value         TEXT
```

Rows are ANDed. "1200–1400m on Dirt, drawn 6 or lower" is three rows. Every
`kind` is a column the archive already has and `query/slices.DIMENSIONS`
already groups by, so nothing new has to be derived to support it.

This buys four things at once:

| | |
|---|---|
| **The band can say WHY today matters** | not "AMAZING KIDS runs today" but "AMAZING KIDS runs today — **first time at 1200m since you booked it**" |
| **The record becomes honest** | runs that did not meet the trigger stop counting against the thesis. A horse booked for 1200m and beaten four times at 1650m has not failed; it has not been tested |
| **`condition_fit` becomes reachable** | `formguide.condition_fit` already answers "how does this horse go under these conditions, with the sample size". Today the Form Guide calls it and the Blackbook does not |
| **Thin tags stop being thin** | see §2.3 |

`pref_distance`, `pref_surface` and `pref_jockey` migrate into it and stop being
write-only columns.

### 2.2 Two tag layers, not one vocabulary

Keep the 19 hand-written tags. They mean something, they are what 196 entries
are labelled with, and the importer was right not to force them into the brief's
proposed taxonomy. But give each one a **mapping to the derived vocabulary**:

```
blackbook_tag_definitions
  tag          TEXT   -- existing
  definition   TEXT   -- existing
  derived_tags TEXT   -- NEW: csv of derive/tags.py names this is the claim about
  polarity     INTEGER -- NEW: +1 the horse ran better than it looks, -1 worse
```

So `traffic` → `short_of_room, checked, crowded, without_cover, hampered`, and
`incident` → the trouble tags at polarity +1.

Once that mapping exists, the BY TAG table can carry a **second column pair**:
the tag's record in *your book* beside the same tag's record *across the whole
archive*. A tag working at 22% on 9 entries beside a base rate computed on 2,400
runs is a completely different reading of the same row — and it is the reading
that says whether the idea is any good independently of whether you picked well.

The artboard already has the space: its BY TAG row is `TAG · ENTRIES · RUNS ·
STRIKE · PLACE · A/E 95% CI · ROI · BACKED vs MISSED`. This adds one column,
`ARCHIVE A/E`, in the same grammar.

### 2.3 Rank the book against the model and the market

The most valuable entry in a blackbook is a horse that **you, the model and the
market disagree about**. The dashboard has all three and the book uses one.

For each booked horse declared today, the band should be able to show:

- the blend's probability and rank (`query/model.blend_breakdown`)
- the de-vigged market probability (`query/pools`)
- and therefore an **agreement flag**: does the model back you up, or are you on
  your own?

This is not a staking signal and must not read as one — `docs/status.md` and the
Model Analysis page are explicit that the model does not beat the market. It is
a *triage* signal. On a card with five booked horses it says which two to look
at first.

### 2.4 Close the loop from the pages that generate theses

Three places in the dashboard produce exactly the observation a blackbook entry
is made of, and none of them can make one:

| page | what it already computes | what it should offer |
|---|---|---|
| **Lookup → Outliers** | runs whose finish most disagrees with the market, and `repeat_horses` for the ones that did it twice | "book this horse" prefilled with the run and the reason |
| **Trials → Standouts** | recent trials rated STANDOUT or POSITIVE | the same, tagged `trial-ability`, with the trial as the source |
| **Results → stewards** | the derived trouble tags on every run | the same, with the matching blackbook tag pre-selected via §2.2's mapping |

The Form Guide already has this (`review.js`, note → promote behind one
deliberate click). It is the right pattern; it is just only in one place.

### 2.5 Review has to have a verdict, not just a prompt

`review_due` fires at four runs since booking and the row says
`REVIEW · n RUNS UNRESOLVED`. There is nothing to click. Resolving means
choosing WON OUT or RETIRE, which are both endings — there is no way to say
"tested, wrong, and here is what I learned".

Proposed: a review action that records a **verdict** —
`VALIDATED / PARTIAL / MISSED`, which is the vocabulary `blackbook_notes`
already uses — against the runs that actually met the trigger. The entry can
then close with evidence rather than with a shrug, and the BY TAG table can
report a tag's verdict mix, which is a much better measure of a *reason* than
strike rate is.

---

## 3. What this does not change

- **The derivation stays.** Runs since booking are read from `runners`, never
  from what anyone remembered to log. That is the single best thing about the
  current module (355 subsequent runs recovered against 27 logged) and nothing
  here touches it.
- **BACKED vs MISSED stays where it is.** Brief 06 calls it the most important
  feature on the page and it is a join over the ledger, so it needs nothing.
- **Weak evidence keeps looking weak.** Every figure added above carries its n
  and its interval, and `thin` stays at 20 runs.
- **The layout stays.** Row, chips, expanded panel, BY TAG table, the two
  analysis panels. This is a proposal about what the columns MEAN, not about
  moving them.

---

## 4. Suggested order

Each step is useful on its own and none blocks the next.

1. **Triggers** (§2.1) — the structural change. Unlocks the honest record and
   the "why today matters" band line. Biggest single win.
2. **Promote from Outliers and Trials** (§2.4) — small, self-contained, and it
   is what turns the book from something you feed by hand into something the
   dashboard feeds.
3. **Tag mapping and the archive column** (§2.2) — answers "is this reason any
   good" rather than "did I pick well".
4. **Model agreement on the band** (§2.3) — triage on a busy card.
5. **Verdicts** (§2.5) — needs §2.1 to know which runs were a fair test.

---

## 5. Open questions for the owner

1. **Should a run that misses the trigger count against the record at all?**
   Argument for dropping it: it was never a test. Argument for keeping it
   visible but uncounted: a horse that keeps being asked the wrong question is
   itself worth knowing about. Recommendation: **show it, greyed, uncounted**,
   with the whole-record figure still available behind a toggle.
2. **Do the 19 existing tags survive as-is?** Recommendation: yes, with the
   mapping in §2.2 doing the reconciliation. Renaming them would orphan 196
   entries' meaning for a tidier list.
3. **How hard a trigger?** A soft trigger ("prefers") is advisory; a hard one
   ("only") could hide the horse from the band entirely on a day it does not
   qualify. Recommendation: **soft by default**, with hard as a per-entry flag,
   because a hidden horse is how you miss one.
