# Handover: doubles and pool turnover

Branch: **`feat/pools-turnover-doubles`** on GitHub. Read `AGENTS.md` first;
this only covers what is on this branch and what is left to do with it.

The owner is not deploying from the machine this was written on. Everything
below is code review, verification and a deploy someone else runs.

---

## 1. What is on the branch

Two pools the GraphQL capture already had access to and never asked for.

| New file | What it does | Table |
|---|---|---|
| `hkrd/ingest/doubles.py` | DBL odds, one request | `odds_doubles` |
| `hkrd/ingest/turnover.py` | `investment` per pool, one request | `odds_pool_turnover` |
| `hkrd/query/money.py` | dollars per runner, pool growth | — |
| `pools.doubles_conditional` | what leg money says about the second race | — |

Endpoints: `/api/money/{date}`, `/api/money/{date}/{race_no}`,
`/api/doubles/{date}/{leg_no}`.

Both ride on `ingest/odds.py`'s endpoint, session and meeting-id guard rather
than repeating them, and both run on the **pair cadence** — only on a tick that
was already reaching HKJC, so an idle minute costs nothing. `--no-doubles` and
`--no-turnover` switch either off.

`ops/crontab` needs no change: `scrape_odds` already runs every minute and
these ride on it. Merging and deploying is enough to start capturing.

Merged with `main` at `13400d6` and green: **1243 passed, 2 skipped**.

---

> **STATUS, 2026-09-08.** Reviewed, verified, merged into `main` and deployed.
> The separator in §2 is **confirmed against a live selling pool** — see below.
> Four things were added on top of the branch and are described in the merge
> commit: money per runner in every pool, money per PAIR (which is what a QQP
> ticket is struck into), a merged-pool guard so a race total does not
> double-count, and the dashboard surfaces that were missing entirely. The
> superseded branch in §6 has **not** been deleted — that is the owner's call,
> not an agent's.

## 2. The one thing that is NOT verified, and how to verify it

> **VERIFIED 2026-09-08 14:58.** `MTG_20260909_0001DBL1` was `START_SELL` with
> 120 nodes and every `combString` was of the form `01/01`. `fetch_doubles`
> returned **936 rows across all seven legs** with none dropped, so the happy
> path is confirmed and not only the failure path. The format is now pinned by
> a test rather than by a live call, because a test that reaches HKJC fails
> when the network does and the fact being pinned is about the format.

**A double's combination separator is `/`, not `,`** — `"02/04"` where a
quinella is `"02,04"`.

That comes from HKJC's own bundle, not from live data:

```js
"DBL" === o && (v = e.combString.split("/"))
```

No double pool was selling while this was written (`sellStatus: STOP_SELL`,
`oddsNodes: []` on every leg), so the format could not be read off a real
reply. It matters because `odds._combination` splits on a comma and returns
nothing for every node — reusing it reports a **quiet pool rather than an
error**. `double_rows` raises if nodes arrive and none of them parse, so the
failure is loud, but the happy path is still unconfirmed.

**To confirm it**, once a market is selling (HKJC opens one around midday the
day before racing):

```bash
python -c "
from hkrd.ingest import doubles
rows = doubles.fetch_doubles('YYYY-MM-DD', 'HV')
print(len(rows), rows[:3])"
```

Non-empty rows with sane horse numbers means the separator is right. An
`OddsError` naming the separator means it is not.

---

## 3. What is claimed and what is not

**Claimed:** the arithmetic. `pool x de-vigged share` is the actual number of
dollars, not an estimate — in a pari-mutuel pool the dividend is
`pool x (1 - takeout) / stake`, so the share IS the money share. Tested.

**Not claimed:** any edge. Nothing captured either pool before this branch, so
there is no archive to measure them against. Do not put either in front of a
betting decision yet; let them accumulate first.

Two things that are easy to misread and are documented at their call sites:

* **Turnover is a denominator, not a tip.** On 2026-09-06 race 3 held
  $4,288,122 of win money against ~$500,000 in every other race on the card,
  because KA YING RISING was 1.0 in a six-horse field. Following that money
  means backing an odds-on shot into a 17.5% takeout.
* **Compare `implied_pct`, never `implied_odds`.** A double carries one takeout
  where two win bets carry two, so the raw implied price sits above the second
  leg's own win price for the whole field. Reading them side by side invents an
  overlay on every runner.

---

## 4. Working with the endpoint

The whitelist and the schema fail **differently**, and the difference is a
usable probe:

| Reply | Means |
|---|---|
| HTTP 400, "doesn't match the schema" | the field does not exist |
| HTTP 200, `WHITELIST_ERROR` | the field exists; this query text is not one the site sends |

Schema validation runs first. That is how `pmPools.investment` was found to be
HKJC's word for turnover, without introspection.

`turnover.INVESTMENT_QUERY` is lifted from the site's bundle character for
character. A trimmed but perfectly valid subset of it comes back
`WHITELIST_ERROR`, so **do not tidy its indentation or drop the `poolInvs:`
alias**. There is a test asserting its shape.

If a query ever starts failing, the bundle hash rotates — get the current one
from the page HTML (`static/js/main.<hash>.js`) rather than assuming the
filename.

---

## 5. Two design points a reviewer will want to check

**Why DBL is a separate call, not another entry in `odds.POOLS`.**
`pools_to_payloads` requires every pool it reads to name exactly one race and
raises otherwise. That is the right rule for the pools it serves and the exact
opposite of what a double is, so adding DBL there would make a correct guard
fire on correct data.

**Why `odds_doubles` is not sorted.** `odds.pair_rows` stores a quinella
smallest-first so one bet cannot have two contradictory rows. A double is
ordered — first-leg 3 with second-leg 7 is a different bet, at a different
price, from 7 then 3 — and sorting collapses two prices into whichever was
written last. There is a test for this.

Also: `null` turnover is not `0`. A pool that has not opened reports null; one
that is open and untouched reports 0. Conflating them shows the whole pool
arriving the instant a card opens, and every growth figure is then measured
from a fiction.

---

## 6. Housekeeping

**Delete `claude/quinella-doubles-odds-scraping-b2147c`.** It is a superseded
first attempt: built on `13e143e`, browser-based, and it collides with `main`
on 13 files including `query/pools.py`. **Do not merge it.**

It is worth reading its commit message once before deleting, for two findings
that are not carried over into this branch:

* Over 69 archived races, a horse's share of the quinella pool against what its
  win price implies is monotonic in outcome — A/E 0.48 / 0.64 / 1.04 / 1.52 by
  quartile, and below 0.85 it is 7 winners against 18.0 expected (Poisson
  p = 0.003). Only the cold end holds up; the top quartile's ROI is one 20/1
  winner and turns negative with two removed.
* The *change* in that ratio predicts nothing measurable (399 observations, no
  gradient), so an alert on movement would be noise. The level is the signal.

Those are worth rebuilding on top of `main`'s `pair_probabilities`, as their
own change with their own review — not folded into a capture commit.
