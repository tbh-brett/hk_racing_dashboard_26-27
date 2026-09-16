# Brief: reading the 25-26 Roster Audit Vault into the dashboard

**2026-09-16.** The vault lives on the owner's PC and a cloud session cannot
reach it. This is the brief for a Claude Code session running locally, in this
folder, with the vault alongside it.

Open it with:

```powershell
cd C:\Users\tbhbr\hk_racing_dashboard_26-27
claude
```

and start by saying: *"read AGENTS.md, docs/start-here.md and
docs/handover-vault.md, then read my 25-26 Roster Audit Vault at <path>"*.

---

## 1. What the vault is, in the owner's words

> An Obsidian vault "essentially to look back at the previous season to seek out
> patterns, mispriced bets from last season (using only official final win odds,
> only available information regarding price unfortunately), jockey and trainer
> stats and partnership, different phases of the season, outstanding performers,
> etc. Identifying patterns to predict what might occur in this season (26-27)."

So it is a season post-mortem done by hand, and the question is which of it the
dashboard should absorb and which of it the dashboard already answers better.

**Both halves matter.** Do not assume everything in the vault needs building.
Several of its questions are already computable here and the honest outcome for
those is a saved view, not a new feature.

---

## 2. Three things to know before reading a single note

### 2.1 "Only official final win odds" is not a limitation here — it is the right price

The owner flags it apologetically. It is not a compromise in this system:

- **Settlement is HKJC tote.** You are paid the final dividend regardless of
  when the bet was struck — verified on 142 of 160 matched historical bets
  (`AGENTS.md`, Betting rules).
- **Therefore early-price value is not capturable**, and the same file says so
  outright: odds movement is a sizing input and an operational signal, *never* a
  timing edge.

So an analysis built on final win odds is built on the only price that ever paid
anybody. Say this back to the owner — it reframes the vault from "what I could
scrape" to "the correct denominator".

The dashboard's `derive/probability.actual_over_expected` already de-vigs final
win odds against the race's own book and returns a 95% interval. That **is** the
"mispriced bets" measurement, done properly.

### 2.2 The partnership analysis is already computable — do not rebuild it

`query/slices.pivot` crosses any two of `DIMENSIONS`, and that dict already
holds `jockey`, `trainer`, `venue`, `course`, `surface`, `going`, `class`,
`distance`, `draw`, `style`, `pace`, `field size` and `month`.

```python
from hkrd.query import slices
slices.pivot("jockey", "trainer", metric="ae")
```

That is jockey × trainer partnership, measured against the price, with every
cell carrying its `n`, with thin cells dimmed rather than hidden, and with
`expected_notable` printed beside the cell count so twenty cells cannot produce
one "finding" by accident.

**If the vault's partnership tables agree with this, the work is a saved Lookup
view and a note in `docs/decisions.md` — not a feature.** If they *disagree*,
that is the interesting outcome and worth chasing: find out which is right
before building anything.

### 2.3 This repo is hostile to manufactured findings, and the vault is the
exact activity that manufactures them

`query/slices.py` quotes the brief on itself: *"A pivot is the easiest way in
this whole tool to manufacture a false finding."* A prior pass over 153
tag × condition cells found 8 clearing significance where **7.0 were expected
by chance**.

A season's hand analysis will contain patterns that are noise. Some of them will
be ones the owner already believes. The job is not to implement them — it is to
**re-measure each one against the archive** and report honestly, including
when the answer is "this does not survive its own sample size".

Every figure that reaches a page must carry its `n` and its interval. No bare
rates. `MIN_SAMPLE` and the `thin` flag exist for this.

---

## 3. What to do, in order

### Step 1 — Inventory, do not synthesise

Read every note. Produce `docs/vault-inventory.md`: one row per distinct claim
in the vault, with

| column | meaning |
|---|---|
| claim | the pattern, in the owner's words |
| kind | `measurable now` / `measurable after work` / `not measurable here` |
| where | the query or dimension that would answer it |
| sample | how much data the vault's own version rested on, if it says |

Do **not** decide anything yet. Show this to the owner first — it is the map,
and they will know which claims they actually act on.

### Step 2 — Re-measure the `measurable now` rows

For each, run it against the archive and record: the vault's figure, the
archive's figure, the `n`, and whether the interval clears. Expect
disagreements; they are the point.

Write the results into `docs/decisions.md` in the existing style — measured
figures, named sources, and what was decided. Not a new document per finding.

### Step 3 — Only then, propose what to build

The two gaps already identified from the cloud session:

- **Season phase is not a dimension.** `query/period.py` knows a season runs
  September to July and `slices.DIMENSIONS` has `month`, but nothing groups by
  *phase* — early / mid / late, or "before and after the international meeting".
  The vault's "different phases of the season" would need this. It is a small
  addition to `DIMENSIONS` if the re-measurement supports it.
- **"Outstanding performers" has no home.** `slices.outliers` finds runs whose
  finish most disagrees with the market, and `repeat_horses` names the ones that
  did it twice — which is close, but it is per-run and the vault's version
  sounds per-horse-per-season. Check before building.

Propose in `docs/proposal-*.md`, following `docs/proposal-blackbook.md`. Get
agreement before implementing.

### Step 4 — Where a finding belongs on a page

Prefer, in this order:

1. **An existing page gains a column or a filter.** Cheapest, and it puts the
   finding where the question is already being asked.
2. **A Blackbook condition** (`query/triggers.KINDS`). If the vault's pattern is
   "this kind of horse under these circumstances", it is a trigger, and the
   Blackbook now measures the record over the runs that met it. See
   `docs/proposal-blackbook.md` §2.1 and `docs/decisions.md`.
3. **A new page.** Last resort. Nine pages is already a lot to keep true.

---

## 4. What not to do

- **Do not port a number without re-measuring it.** A figure from the vault is a
  hypothesis about the archive, not a fact about it.
- **Do not build a staking rule.** The Model Analysis page states plainly that
  the model does not beat the market, and `docs/start-here.md` §8 says the
  staking advice the old system implied was never supported by its own numbers.
  A season pattern does not change that.
- **Do not add a dependency** to read the vault. It is Markdown; `pathlib` and
  the `Read` tool are enough.
- **Do not copy the vault's contents into the repo.** Findings and decisions go
  into `docs/`; the owner's notes stay the owner's notes, and `AGENTS.md`
  (Secrets) keeps bet logs and account data out of the repository entirely.

---

## 5. What already shipped, so the local session does not redo it

Branch `claude/practical-knuth-a95z9f`, three commits:

| | |
|---|---|
| `fc05473` | Expiry replaced by retire. `closed_date` records WHEN a thesis was closed; retired entries stopped being highlighted on Race Day and the Form Guide; reopening takes a new thesis and keeps the old one on `blackbook_status_log`. |
| `237ee5c` | `docs/proposal-blackbook.md` — what the module should become. §2.2–§2.5 are unbuilt. |
| `60a4a10` | `blackbook_trigger`: a thesis is a condition, not just a sentence. The record splits into "every run since" and "the runs that asked the question". |

Pull that branch before starting, and run `python -m pytest` — 1,434 tests, all
passing as of that commit.
