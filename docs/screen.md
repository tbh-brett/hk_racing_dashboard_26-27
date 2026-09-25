# The Screen — who is a chance, before the price

The first section of the Briefing. For every race on a card it gives each
runner a chance to win and to place, worked out from things known before any
odds exist, the measured reasons for and against, and the owner's own material
beside them: the blackbook entry, run and trial notes, the last start in HKJC's
words, the latest trial, and the horses in the field it could turn around.

Code: `hkrd/model/screen.py` (factors, weights, scoring),
`hkrd/model/screen_fit.py` (the fit), `hkrd/query/screen_inputs.py` (what it
reads), `hkrd/query/screen.py` (the payload), `GET /api/screen/{date}`,
`web/assets/briefing-screen.js`. Weights are re-derived by
`python -m hkrd.jobs.fit_screen`.

Measured 2026-09-24 on every settled race from 2020-21 to 23 Sep 2026: 5,019
races, 61,007 runs. Refitted 2026-09-25 once Group and 4-year-old races
carried their class (docs/audit-2026-09-24.md section 2), and again when the
gate was read against the last three starts rather than only the last.

---

## How to read it

- **Shortlist** is the Screen's top four by chance to place. Walk-forward over
  3,366 races (2022-23 to 26/27 so far, each season scored with weights fitted
  only on the seasons before), the top four held the winner **62.2%** of the
  time. Form alone (SARR): 58.6%. The closing tote's top four: 71.0%.
- **Also a case**: outside the four, but its circumstances (everything except
  form and rider) are worth ×1.2 or more.
- **Your book · set-up**: every live blackbook horse, with whether today's
  circumstances suit it — FAVOURABLE, NEUTRAL or AGAINST at ×1.16 either way —
  and its rank in the race. Your written conditions are shown beside it, met or
  not met.
- **Chips** are multipliers on the horse's chance **on top of its SARR
  rating**. ×1.28 means "28% more likely than its form alone says"; the tooltip
  says what fired it.

**It is not a value signal.** Where the Screen rates a horse 1.25–2× the
closing tote's chance, the tote has been right: A/E **0.91**. Beyond 2×, **0.85**
(20,606 runners). The Screen finds the horses worth reading. The price decides
whether one is worth backing. This is the same result the Model Analysis page
reports for SARR.

---

## What earned a place

The weights come from an exploded (first three home) logit fitted with SARR in
the model, so each factor is what it adds *beyond* the form figures. "Stable"
means it pointed the same way in every season it was fitted on alone.

| Factor | × chance | Runs | Stable |
|---|---|---|---|
| Jockey's strike rate over the last year (per logit step) | ×1.63 | all | 6/6 |
| Trial POSITIVE or STANDOUT since the last run | **×1.67** | 832 | one season |
| New stable since the last start | ×1.28 | 1,119 | 6/6 |
| The only habitual leader in the race | ×1.28 | 1,299 | 6/6 |
| One of two habitual leaders | ×1.24 | 2,928 | 6/6 |
| Rating up 3+ since the last start | ×1.18 | 1,584 | 2/2 |
| Raced wide last start | ×1.11 | 17,923 | 6/6 |
| **Gate inside its last 3 starts, where the last start alone misses it** | **×1.21** | 2,811 | 6/6 |
| Drawn 4+ gates further in than last start | ×1.13 | 15,059 | 6/6 |
| Habitually on the pace | ×1.10 | 7,434 | 5/6 |
| Fifth run or later this campaign | ×1.07 | 27,332 | 5/6 |
| Beaten last start, but held up, checked or blocked | ×1.06 | 6,574 | 5/6 |
| Up in class | ×1.05 | 3,102 | — |
| One of three or more habitual leaders | ×1.02 | 5,544 | — |
| First-up | ×0.89 | 7,985 | 4/6 |
| Drawn 4+ gates further out | ×0.90 | 15,111 | 6/6 |
| Gate outside its last 3 starts, where the last start alone misses it | ×0.94 | 2,534 | 4/6 |
| Habitually at the back | ×0.91 | 26,673 | 6/6 |
| Other course from last start | ×0.92 | 15,480 | 6/6 |
| Beaten out of the frame last start | **×0.73** | 32,660 | 6/6 |
| Trial NEGATIVE since the last run | ×0.74 | 682 | one season |
| Second-up after running 7th or worse first-up | **×0.72** | 4,746 | 6/6 |
| Veterinary finding after the last start | ×0.76 | 606 | 6/6 |
| Rating down 3+ since the last start | ×0.78 | 908 | 2/2 |
| Debut | ×0.67 | 3,020 | 5/6 |

Walk-forward win log loss, lower is better:

| Test season | Form (SARR) | Form + rider | Screen | Closing tote |
|---|---|---|---|---|
| 2022-23 | 2.2425 | 2.1828 | **2.1595** | 1.9693 |
| 2023-24 | 2.2856 | 2.2549 | **2.2216** | 2.0237 |
| 2024-25 | 2.2780 | 2.2434 | **2.2086** | 1.9953 |
| 2025-26 | 2.3012 | 2.2699 | **2.2511** | 2.0581 |
| 26/27, 45 races | 2.2726 | 2.2078 | 2.2138 | 2.0052 |

The Screen beats form-plus-rider in every full season. The rider and the
circumstances each add roughly as much as the other (the rider more in
2022-23 and 2025-26, the circumstances as much or more in 2023-24 and
2024-25), and the tote is still well ahead of both.

### How far back it reads

Almost every factor above reads the **last start only** — the trouble, the vet
finding, the rating move, the class, the stable, the venue. Four more read the
*dates* of earlier runs to work out first-up and second-up, and nothing else
looks further back. That was challenged on 25 Sep 2026: *what about a horse
that has had several bad draws in a row?*

Measured over 60,885 runs against the Screen's own place probability, reading
deeper mostly finds what the form rating has already absorbed:

| Read further back | Runs | A/E |
|---|---|---|
| **Gate better than its last 3, where the last start alone misses it** | 2,811 | **1.15** |
| Gate worse than its last 3 | 10,627 | 0.96 |
| Trouble in 2 of the last 3 runs | 5,720 | 1.02 |
| A wide trip in 2 of the last 3 | 14,740 | 1.02 |
| Bad last start, good the run before | 3,722 | 0.99 |

**The gate is the exception, and the reason is structural.** A compromised run
lowers the horse's rating at the time, and that rating is already in the model
— so a streak of trouble tells it nothing new. A draw is not a fact about a run
the horse has had; it is a fact about the race it is about to run. A horse
drawn 12, 14, 3 and today 4 has had the gate against it all campaign, and
`draw_in` — which compares today with the last start only — sees no change.

Fitted walk-forward, `draw_in_3` points the same way in **all six seasons**
fitted alone, and on the seasons that had not seen it those runs placed at
**A/E 1.18 ± 0.08** (1,888 runs). `draw_out_3` is the mirror at **0.81 ± 0.08**.
Both fire only where the last-start pair is silent, so one fact is never split
across two columns.

### Tags that were measured and left out

The stewards' vocabulary has 39 tags and the Screen reads 13. The rest were
tested. Against the Screen's own probability three looked real — `eased` 0.89,
`weakened` 0.94, and the whole `lane:*` family — and under a walk-forward fit
none survived:

| Tag | Share of runs | A/E vs the Screen | Walk-forward | Verdict |
|---|---|---|---|---|
| `weakened` | 14.0% | 0.94 | 0.91 ± 0.06 | inside its error |
| `bumped` | 9.6% | 1.00 | — | worth nothing at all |
| `lane:wide` | 29.4% | 1.01 | — | worth nothing at all |
| `eased` | 3.5% | 0.89 | 0.94 ± 0.14 | wrong way in 2 seasons of 5 |
| `awkwardly_away` | 4.8% | 1.07 | — | under 2 standard errors |
| `disappointing` | 0.7% | 1.04 | — | too rare to fit |

`prev_beaten` (×0.73) already carries most of `weakened`: a horse that weakened
is usually a horse that finished out of the frame, and the model counts that
once. `bumped` is the striking one — the most common tag the Screen ignores,
on nearly one run in ten, and worth exactly 1.00.

**Still unread, and the one real gap:** HKJC's veterinary page
(`vet_records`). `derive/tags` notes that 60 of 100 vet-page findings carry no
stewards' tag, because the stewards wrote about the run and not the horse — so
a horse can have a cardiac finding the Screen never sees. It cannot be fitted
yet: the scrape starts on 6 Sep 2026 and holds three meetings.

### The things you named, answered

| You asked about | What seven seasons say |
|---|---|
| **Gate** | Matters a great deal to who wins (HV gates 1-3 place at 1.29× the field rate), but SARR already carries the draw. What adds beyond it is the *change* — 4+ gates in ×1.13, out ×0.90 — and, more strongly, the change against its last THREE starts (×1.21). See “How far back it reads” |
| **Too many of the same type** | True for leaders only. Alone ×1.28, one of two ×1.24, one of three or more ×1.02: the edge is gone. Closers do **not** benefit from a crowded speed (×0.96 with three or more leaders, ×0.94 with one or none) |
| **Good trial** | The strongest single factor, ×1.67, and a NEGATIVE trial ×0.74. One season of data only |
| **Same jockey / same gear at the trial** | Nothing. Same jockey ×0.86 on 343 runs; same gear ×1.00 |
| **Superior jockey** | The largest thing SARR cannot see. A top-five rider places at 1.38× what form says, in 7 of 7 seasons |
| **Less weight** | Nothing on its own. In a handicap the weight follows the rating: weight down 5lb+ ×1.04, weight **up** 5lb+ ×1.06 |
| **Weaker competition** | A class **drop** is nothing (×1.02). A class **rise** is ×1.05, inside its own error. It read ×1.09 before Group and 4-year-old races carried their class |
| **Excuses** | Being beaten counts against a horse more than SARR allows (×0.73). An excuse — held up, checked, blocked — gives back a little of it (×1.06). "Raced wide" is the one that helps outright (×1.11) |
| **"The commentator picked it up"** | "Finished off well" is ×1.04 once finishing position is counted, which is nothing. The words are shown on the page; they are not scored |
| **Head-to-head swing** | See below. The weight swing is a trap |

### Head-to-head

Over 91,856 pairs meeting again within a year, the earlier result repeated
**58.3%** of the time. The margin decides most of it: a length or less repeats
51%, a coin toss; 8L+ only 68%.

- **Draw swing** moves it the way you would expect (the beaten horse 6+ gates
  relatively inside: repeat falls to 52%). SARR already knows this.
- **Jockey swing** adds beyond form: the beaten horse on a rider 5+ points
  better than before, relatively, and the repeat falls to 50% where form alone
  says 56%.
- **Weight swing goes the wrong way.** Pairs where the beaten horse is now 8lb+
  *worse* off reversed *more* often (repeat 49%) than pairs where it is 8lb+
  better off (61-62%). The weight follows the rating, so the swing mostly shows
  which horse has been winning since. The Screen's reversal notes name the
  margin, the draw and the rider, and never the weight.

The Race Day head-to-head panel used to sort pairs by weight swing and badge
it at 4, 6 and 8lb. It now follows this: margin, draw and rider, with the
weight shown as context (docs/audit-2026-09-24.md §3).

### Your blackbook

Live blackbook horses placed at **1.23×** what their form rating says (515
runs, interval [1.03, 1.42]). Your eye adds information the figures do not
have. At the closing price, A/E is 1.05, interval [0.76, 1.33]: the market has
most of it too. The set-up verdict is there for your point that a good horse
is not always in a good race.

---

## 26/27 so far

Five meetings, 45 races, scored by weights that had not seen them: the
Screen's top four held the winner 58% of the time, form alone 60%, the tote
82%. Too few races to separate anything, and the season opens almost entirely
first-up (435 of 552 runs), where last-start factors are months old. The
factors themselves are behaving as measured: positive trials placed 26 times
against 21 expected, negative trials 8 against 12.5, top-five jockeys 23
against 13.6, live blackbook horses 28 against 23.7.

## Limits

- **Trials are one season deep** (from August 2025). Refit
  (`python -m hkrd.jobs.fit_screen`) as 26/27 accumulates, and paste the
  printed weights into `model/screen.FACTORS`.
- **Ratings are in the archive from 2024-25 only**, so the rating factors rest
  on two seasons.
- **Scratchings.** The card stores no scratched flag, so a horse withdrawn
  after the card was scraped stays on the Screen until results land. Race Day
  has the same limit.
- **Tipster support is not scored.** It began on 23 Sep 2026 and has no
  history to measure. It is shown as `TIPS n` beside the horse.
- **The three-run gate reading is a mean, so one wide gate can carry it.**
  Gates 11, 4, 1 and today 1 fires `draw_in_3` even though the horse was drawn
  1 last time too, because the 11 lifts the average. The ×1.21 is measured
  over exactly this population, false positives included, so the figure is
  honest — but a variant that also required today's gate to beat the last
  start has not been tested and might be sharper.
- **Habitual style** is the house definition (`derive.pace.habitual_style`,
  15 runs, last run weighted ×3), the one Race Day, the Speed Map and SARR use.
  It calls more horses Leader than a stricter rule would, and the pace weights
  were fitted on it.
