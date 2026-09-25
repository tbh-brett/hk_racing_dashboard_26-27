# The Briefing — how it is put together

The front page. For one meeting it answers, race by race: *what is worth
reading here, and what is the evidence?* It is built from three kinds of
evidence that arrive at different times, from different machines, and are
never blended into one score. The design brief is
`docs/design-prompt-briefing.md`; this is the plumbing under it.

Owner's decisions, 25 Sep 2026: races worth studying first, the rest in race
order; the page follows race day to the last race; Horse Detective counts as
a source; the combined data is built before the new design.

---

## Where each kind of evidence comes from

```
 COMPUTED                    SAID                               PRICED
 (the server)                (this PC → the server)             (the server, and this PC)

 HKJC card, results,         ops/tips.ps1 — task "HKRD tips",   jobs/scrape_odds — the tote,
 trials, stewards            10:00 and 20:30 daily              on the server's schedule
   │  jobs/scrape_meeting      │ harvest_youtube  FC, RTW          │
   ▼                           │ harvest_threads  Horse Detective  │ jobs/scrape_fixed_odds —
 derive/  SARR, pace,          │ harvest_sportsbet (refused        │ Ladbrokes prices and the
          tags, trials         │   since 24 Sep)                   │ Racing & Sports tips
   ▼                           ▼                                   │ (not yet on a schedule)
 model/screen              extract_tips → push_tips                │
   ▼                           ▼  POST /api/tips/import            │
 query/screen              jobs/import_tips — every number       │
                           and name checked against the card;    │
                           a mismatch is held, never guessed     │
                               ▼                                   ▼
                           tipster_selection, connections_quote   odds_snapshots, fixed_odds
                               ▼                                   ▼
                           query/tips_summary  ◄───────────────────┘
          │                    │
          └────────► query/briefing ◄─── query/money, movement, market
                           ▼
                  GET /api/briefing/{date}
```

The PC does what the server cannot: YouTube, Threads' feed and Sportsbet all
refuse the Fly machine and answer a home connection. Everything the PC sends
goes through the same import and the same checks.

## The answer: `GET /api/briefing/{date}`

`query/briefing.meeting(date, now=…)` composes what already exists — the
Screen, the tips summary with every runner priced, the pools, the price moves
and each race's concentration — into one object per race and per runner.
Measured on the real 23 Sep and 27 Sep cards: about 110 ms, 270–370 KB of
JSON before gzip (the app compresses every response).

What it adds on top of its parts:

- **`stage`**: `cold` · `voices` · `priced` · `race_day` · `settled`, from
  what has landed and the date.
- **`clock`**: every source, `in` (and when), `due` (and when it usually
  lands), `overdue`, or `irregular`. The due times are the measured
  publishing clock:

  | Source | Usually lands | Evidence |
  |---|---|---|
  | 賽馬Fact Check | 20:00 two nights before | four meetings (ops/tips.ps1) |
  | Racing To Win preview and interviews | about 16:00 the day before | the tips work (ops/tips.ps1) |
  | HKJC tote | about midday the day before, thin until race day | AGENTS.md |
  | Racing & Sports, Ladbrokes, Sportsbet | by race-day morning | **seen once** (23 Sep) |
  | 神探賽馬 Horse Detective | race-day morning, when it posts at all | one post (13 Sep, 11:49) |

- **`reasons`** per race, each of one kind:

  | Key | Kind | Fires when |
  |---|---|---|
  | `lone_leader` | computed | one habitual leader in the field (×1.28, 6/6 seasons) |
  | `book` | computed | a live blackbook horse whose set-up is FAVOURABLE or whose written conditions are met |
  | `trial` | computed | a shortlist or "case" horse trialled well since its last run (×1.67, one season) |
  | `case` | computed | horses outside the Screen's four whose circumstances are worth ×1.2+ |
  | `interview` | said | a jockey or trainer spoke about a runner |
  | `consensus` | said | three or more sources back one horse |
  | `talked_up` | said | two or more sources back a horse the Screen ranks outside its four |
  | `screen_alone` | said | the Screen's first choice is backed by none of the (2+) sources that tipped the race |
  | `price_gap` | priced | a market pays 5%+ above another market's fair chance |
  | `late_money` | priced | a price firmed 10%+ in its last minutes |
  | `market_apart` | priced | the Screen's top two sit 7th or worse in the betting |

- **`order`**: races by how many DIFFERENT reason keys they have, then race
  number; races already run go last.

### Rules it keeps

- **Three kinds, never one number.** A runner carries `screen`, `support` and
  `price` side by side. Nothing adds them.
- **A price before race day is shown, never reasoned from.** The day-before
  tote is priced by a handful of bets (AGENTS.md: on 9 Sep a near-empty pool
  priced a runner at 2.2 that raced at 10.0). The priced reasons and the
  `edges` list start on race day, which is also the baseline every move is
  measured from.
- **`market_apart` says what is known about it**: where the Screen rates a
  horse well above the tote, the tote has been right (A/E 0.85–0.91,
  docs/screen.md). It is a reason to read the race, not a reason to back the
  horse.

## What is not measured yet

- **The order.** A count of reason kinds is a reading order, not a finding.
  Worth checking after a few meetings: are the races it puts first the ones
  the owner ends up studying? Is the Screen's shortlist any sharper in them?
- **Tipster support** has no history to score (it began 23 Sep 2026). It is
  shown and counted, never weighted.
- **The bookmakers' and Horse Detective's timing** rest on one observation
  each.

## Open items

1. **The page.** `web/pages/briefing.html` still stacks the Screen
   (`/api/screen`) over the board (`/api/tips/summary` plus eleven race-card
   fetches). It moves to `/api/briefing` when the new artboard comes back
   from Claude Design, and the second race list goes with it.
2. **Following the day** needs the endpoint to answer conditional polls
   (ETag, `poll_after`) the way the Race Day card does, so a phone at the
   course re-reads only what changed.
3. **Horse Detective** shows only its latest four posts and nothing that
   scrolls off can be fetched again. The PC reads it at 10:00 and 20:30; its
   one analysis post went up at 11:49 on race day. A race-day poll every 30
   minutes would be safer — a second scheduled task, not yet created.
4. **Sportsbet has refused this PC too since 24 Sep.** Racing & Sports'
   tips and race comment still come through Ladbrokes; Sportsbet's prices
   and its form line on every runner do not.
5. **Ladbrokes on the server**: `jobs/scrape_fixed_odds` is built and not yet
   in `ops/crontab`.
6. **Nothing here is deployed.** Production runs `tips-layer` @598bf81. The
   `briefing` branch holds the tips layer's later work, the Screen, and this.
