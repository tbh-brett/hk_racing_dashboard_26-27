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
 (the server)                (this PC → the server)             (the server)

 HKJC card, results,         ops/tips.ps1 — task "HKRD tips",   jobs/scrape_odds — the tote,
 trials, stewards            10:00 and 20:30 daily              on the server's schedule
   │  jobs/scrape_meeting      │ harvest_youtube  FC, RTW          │
   ▼                           │ harvest_threads  Horse Detective  │ jobs/scrape_fixed_odds —
 derive/  SARR, pace,          │                                   │ Ladbrokes prices and the
          tags, trials         │                                   │ Racing & Sports tips,
   ▼                           ▼                                   │ every 30 min on race day
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

The PC does what the server cannot: YouTube and Threads' feed refuse the Fly
machine and answer a home connection. Everything the PC sends goes through
the same import and the same checks.

**Sportsbet was taken out on 30 Sep** (Brett): it printed the same Racing &
Sports words and picks as Ladbrokes, it had refused this PC as well as the
server since 24 Sep, and it was the only source of the per-runner "R&S FORM"
line (Ladbrokes' feed has the field and leaves it empty for Hong Kong), which
went with it. The tips payload no longer carries prices; Ladbrokes' come from
the server's own capture.

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
  | HKJC tote | read from midnight on race day | `jobs/scrape_odds`: the day-before pool is a handful of bets and is not captured, so a clock expecting it at noon the day before read "overdue" every time (fixed 30 Sep) |
  | Racing & Sports, Ladbrokes | by race-day morning | **seen once** (23 Sep) |
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

## The page

`web/pages/briefing.html`, ported from Claude Design's artboard of 25 Sep
(`web/design-source/Briefing.dc.html`, bound to the four real samples of
this endpoint). `briefing-model.js` reads the answer into a view,
`briefing-desk.js` and `briefing-phone.js` draw it; below 1000px the phone
list is shown. It re-reads every minute on race day while a race is still to
run and every ten minutes otherwise. `?as_of=YYYY-MM-DDTHH:MM` pins the page
to a moment — the stage, the clock and the minutes to each off as they stood
then — for looking back, and for checking each stage.

`briefing-format.js` holds the pure formatting and mark helpers, split out
of the model at its 500-line mark on 28 Sep.

**After the off.** A race that has run collapses to one line — its winner,
where the Screen had it, who named it, and how the Screen's first choice
finished — and opens, like any race, to the Screen's order with where each
horse finished beside it (`FIN`). The race the address names (the shared
header writes race 1 there on every load) is not opened once it has run; a
race someone clicks always is. `briefing-after.css` styles both this and the
record below.

## The sources' record: `GET /api/tips/record`

Brett, 28 Sep: "track how good each source performs". Below the panels,
every source's record this season (`query/tips_record`,
`briefing-record.js`), on races with a result only:

- **Top pick**, one a race: the pick ranked first, or the only horse the
  source named in that race. Won and placed out of how many, the return of
  $10 on each at the final tote price, A/E (wins against what the closing
  tote expected of the same horses, de-vigged), and what the tote favourite
  did in the same races.
- **Every horse named**: won and placed. Interviews (the horses the
  connections were asked about) and Fact Check's featured horses have no top
  pick: a conversation is not a tip.
- **Benchmarks**, scored the same way: the tote favourite (the market's four
  shortest as its "every horse"), and the Screen's first and its four.

Two rules keep it honest:

1. **A race's tips are frozen at the off.** Once HKJC shuts a race's pool
   (`market_close`) or its result is stored, an import can add to that race
   but never rewrite or remove what it holds (`store/tips.races_gone_off`);
   the Ladbrokes job pushes Racing & Sports every half hour until 22:30,
   hours after the last race. A pick whose own publish time is after the off
   is not counted (`late`); one on a non-runner is not scored (`scratched`).
2. **The Screen is scored on what it said, on races it never saw.** When a
   meeting's result lands, the nightly job records the Screen's order for it
   (`jobs/record_screen`, table `screen_pick`), and nothing rewrites that row
   — a refit would otherwise rescore every race it has been judged on.
   Meetings on or before the fit date (`model.FIT["fitted"]`, 25 Sep) are
   never recorded: the weights were fitted on them.

The record is not sorted by how anyone has done, and every figure carries
its sample. Six meetings in, one winner moves a strike rate by several
points.

**Backfilled 28 Sep.** Every meeting this season, 6 to 27 Sep, was extracted
again from videos published before each meeting and pushed. Nothing had
reached the dashboard before 27 Sep because `.env` held an empty password,
and Racing To Win's previews were never fetched. Fact Check resolves only
where a runner's Chinese name is known: HKJC serves no race card for a
past meeting, so 6 to 16 Sep resolve only the horses whose names were
learned from later cards.

## What a video discussed: sections, under each runner

Brett, 30 Sep: "whatever is discussed is summarised, identified and included
on the dashboard". Fact Check's previews talk about jockeys, stables and the
meeting as much as about any one horse — the 10.1 preview on Badel back from
suspension with 羅富全's three rides, 伍鵬志's six meetings in a row, 希斯's
day racing — and the per-horse quotes left all of that out.

`tools/extract_factcheck.py` now keeps every section of a preview (the
subtitles split where the video pauses for five seconds; a one-line heading
joins the section after it) as a quote on no runner, `topic` 'section', with
the card's Chinese names of the runners it names. `query/tips_talk` hands
each section to every runner it names, and the Briefing shows it in that
runner's SAID column beside the source's own comment and picks: folded to
its first line, marked as context (dotted, never a vote), with the horse's
own comment cut to 〔…上面〕 so it is not read twice, and "named in it, not
counted" where the source said nothing else about the horse. So VICTORY
CHAMPION carries the Badel section and GOOD FORTUNE 伍鵬志's.

It was drawn first as a panel of its own above the races (30 Sep, v52);
Brett, the same evening: the runner's expanded row, source and quote, is the
place for it, and the panel came out.

Two extraction fixes came with it, measured on the 9.23, 9.27 and 10.1
previews: a name right after 比 / 贏 / 勝 / 輸俾 / 擊敗 (optionally with 廐侶)
is the horse another was measured against or beat, and opens no comment
(「只比廐侶飛鷹翱翔…」 had handed DROMBEG BANNER GOOD FORTUNE's 7yo maiden win
and 125lb); and a single line on its own is a mention, not a segment
(「包括當時亞軍精算暴雪」), shown in its section and not counted as featured.

A video read before its captions were ready is asked again for a week
(`harvest_youtube.incomplete`): the 10.1 preview was read at 20:31 on 28 Sep,
31 minutes after it went up, with its subtitle track listed and empty, and
was "already on disk" to every run after — so 1 Oct had no Fact Check until
30 Sep.

## Open items

1. **Conditional polling.** The race-day re-read is a full answer each
   minute (about 60 KB gzipped). An ETag/`poll_after` like the Race Day
   card's would make an idle minute free.
2. **Horse Detective's feed has gone stale.** Open RSS last rebuilt it on
   21 Sep; its two picks of 23 Sep (R3 #2, R7 #1) never reached it, and every
   poll since has read the same four posts. `harvest_threads.py` now reports
   the feed's build date and says STALE past two days. Nothing here can make
   the service rebuild, and Threads' robots.txt forbids reading the profile
   by script. It posted nothing for 27 Sep.
3. **A 27 Sep Fact Check row is known to be wrong and is kept.** Before
   30 Sep, 「後上鬥贏錶之銀河」 (another horse beat 錶之銀河) filed a quote under
   SILVERY GALAXY (R8 #1), and two one-line mentions (嘉嘉友福 at 1:19,
   包裝天王 at 1:22) counted as featured. The race has run, so its tips are
   kept as they stood. The SILVERY GALAXY row was removed by hand on 30 Sep
   at Brett's request (quote gKRxEjlKMUI:170); the two mentions stay.
