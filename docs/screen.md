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
races, 61,007 runs.

---

## How to read it

- **Shortlist** is the Screen's top four by chance to place. Walk-forward over
  3,366 races (2022-23 to 26/27 so far, each season scored with weights fitted
  only on the seasons before), the top four held the winner **62.4%** of the
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
closing tote's chance, the tote has been right: A/E **0.91**. Beyond 2×, **0.86**
(20,600 runners). The Screen finds the horses worth reading. The price decides
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
| The only habitual leader in the race | ×1.28 | 1,297 | 6/6 |
| One of two habitual leaders | ×1.24 | 2,938 | 6/6 |
| Rating up 3+ since the last start | ×1.17 | 1,584 | 2/2 |
| Raced wide last start | ×1.11 | 17,923 | 6/6 |
| Drawn 4+ gates further in than last start | ×1.11 | 15,059 | 6/6 |
| Up in class | ×1.09 | 2,692 | 5/6 |
| Habitually on the pace | ×1.09 | 7,431 | 5/6 |
| Fifth run or later this campaign | ×1.07 | 27,332 | 5/6 |
| Beaten last start, but held up, checked or blocked | ×1.06 | 6,574 | 5/6 |
| One of three or more habitual leaders | ×1.02 | 5,537 | — |
| First-up | ×0.89 | 7,985 | 4/6 |
| Drawn 4+ gates further out | ×0.89 | 15,111 | 6/6 |
| Habitually at the back | ×0.91 | 26,674 | 6/6 |
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
| 2022-23 | 2.2425 | 2.1828 | **2.1605** | 1.9693 |
| 2023-24 | 2.2856 | 2.2549 | **2.2205** | 2.0237 |
| 2024-25 | 2.2780 | 2.2434 | **2.2084** | 1.9953 |
| 2025-26 | 2.3012 | 2.2699 | **2.2530** | 2.0581 |
| 26/27, 45 races | 2.2726 | 2.2078 | 2.2112 | 2.0052 |

The Screen beats form-plus-rider in every full season. The rider and the
circumstances each add roughly as much as the other (the rider more in
2022-23 and 2025-26, the circumstances as much or more in 2023-24 and
2024-25), and the tote is still well ahead of both.

### The things you named, answered

| You asked about | What seven seasons say |
|---|---|
| **Gate** | Matters a great deal to who wins (HV gates 1-3 place at 1.29× the field rate), but SARR already carries the draw. What adds beyond it is the *change*: 4+ gates in ×1.11, out ×0.89 |
| **Too many of the same type** | True for leaders only. Alone ×1.28, one of two ×1.24, one of three or more ×1.02: the edge is gone. Closers do **not** benefit from a crowded speed (×0.96 with three or more leaders, ×0.94 with one or none) |
| **Good trial** | The strongest single factor, ×1.67, and a NEGATIVE trial ×0.74. One season of data only |
| **Same jockey / same gear at the trial** | Nothing. Same jockey ×0.86 on 343 runs; same gear ×1.00 |
| **Superior jockey** | The largest thing SARR cannot see. A top-five rider places at 1.38× what form says, in 7 of 7 seasons |
| **Less weight** | Nothing on its own. In a handicap the weight follows the rating: weight down 5lb+ ×1.04, weight **up** 5lb+ ×1.06 |
| **Weaker competition** | A class **drop** is nothing (×1.02). A class **rise** helps (×1.09), because the horses going up are the improvers |
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

This is at odds with the Race Day head-to-head panel, which sorts pairs by
weight swing and badges it at 4, 6 and 8lb. That panel is untouched here.

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
- **Habitual style** is the house definition (`derive.pace.habitual_style`,
  15 runs, last run weighted ×3), the one Race Day, the Speed Map and SARR use.
  It calls more horses Leader than a stricter rule would, and the pace weights
  were fitted on it.
