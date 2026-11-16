# Where each import came from

Brett, 28 Sep 2026: CHIU CHOW GOLF, a horse he picked out at the trials,
won at 20/1 on 27 Sep. Its HKJC profile says it was owned by Price
Bloodstock Management — David Price's operation, whose horses "usually,
eventually perform" — and Price had tipped it in his circle. Can these pages
be read, and does the agent, the previous trainer, the previous owner or
where the horse was trained say anything about how it runs here?

## The source

HKJC's **Intro to New Horses**
(`racing.hkjc.com/en-us/local/page/new-horse?racedate=…&raceNo=…&brandNo=…`):
one profile per import, written for the race it is first declared for and not
revised. The page is a shell; one JSON endpoint serves the index (every
profile by race date, with brand number and address) and each profile as
HTML (`ingest/newhorse`).

- **It goes back to September 2025 and no further.** The 2024/25 folders
  answer empty. So this is about 600 horses, not thousands, plus ten to
  twenty a week.
- **It is written by HKJC's contractor, in prose**, and says so. Import
  type, origin, pedigree, the sire's HK record and the sale history are
  laid out; who owned and trained the horse, its agent and how it trialled
  or raced are sentences — "owned by Price Bloodstock Management Ltd that
  under Allan & Jason Williams' yard in Australia". They are read by rule
  (no model: Brett, 2026-09-22). On the 612 profiles: previous trainer 74%,
  country trained in 77%, sale price 74%, a named agent 11%.
- **Agents** appear as an owner or a buyer. Every named bloodstock agent is
  kept, aliases folded (`David Price` and `Price Bloodstock Management` are
  `Price Bloodstock`).

## What 2025/26 showed

Every profiled horse joined to its HK runs (archive to 23 Sep 2026): A/E is
wins against the closing tote's de-vigged chances of the same runs; rating is
the median change from first run to last; rose is how many gained ten points.

| | Horses | Won | A/E | Rating | Rose 10+ |
|---|---|---|---|---|---|
| No agent named | 444 | 171/2497 | 1.01 | −2 | 25/350 |
| **Price Bloodstock** | 20 | 14/108 | **1.36** | 0 | **4/16** |
| John Foote Bloodstock | 9 | 12/55 | 1.65 | +9.5 | 3/6 |
| Magus Equine | 6 | 5/28 | 2.19 | 0 | 1/5 |
| Upper Bloodstock | 10 | 2/48 | 0.60 | −3 | 0/10 |
| Trained in Australia | 219 | 93/1178 | 1.05 | −2 | 17/157 |
| Trained in New Zealand | 113 | 39/640 | 0.98 | −3 | 2/96 |
| Trained in UK/Ireland | 34 | 20/199 | 1.51 | −2 | 2/31 |
| PPG / PP | 310 / 181 | 133/1585, 66/1128 | 1.04 / 1.04 | −2 / −3 | |
| Sold under A$60k | 85 | 19/456 | 0.84 | −3 | 2/67 |
| Won a pre-import trial | 126 | 60/639 | 1.09 | −2 | 11/94 |

- **The market already prices** import type, the sire's HK record, the sale
  price and a pre-import trial: A/E near 1.
- **Named agents are the one thing it may not**, and they differ: Price,
  John Foote and Magus Equine above; Upper Bloodstock below. The agent, not
  having one. Price's horses win races about as often as other imports; what
  stands out is upside — 4 of 16 rose ten points or more (Sight Hermoso
  52→82, Fit For Beauty 52→73, Elite Golf 52→67), against 25 of 350.
  Price's horses come through three yards (Ben, Will & JD Hayes; Allan &
  Jason Williams; Richard & Chantelle Jolly), so the previous trainer
  mostly repeats the agent.
- **UK/Ireland** beat the market on a handful of long-priced winners
  (158/1, 52/1, 32/1), and their ratings fell. Weak.

**None of it is proven.** One season, some forty groups looked at, so a few
will look good by chance; Price's 4-of-16 would come about by luck roughly
one time in ten. An idea formed on these numbers cannot also be tested on
them, so the record keeps the imports profiled from 2026/27 on — the test —
apart from 2025/26, where the idea came from.

## What the dashboard does with it

Brett's choices, 28 Sep: show and score; every named agent; the first five
starts; no manual "whispers".

- `jobs/scrape_background` fills `horse_background` from the index — once
  for everything listed, then daily at 13:50 for what is new (`ops/crontab`).
  A horse profiled twice (its first declaration scratched) is read again
  only for the newer profile.
- **The background line** — import type, where it was foaled and trained,
  former trainer and owner, agent, sale, overseas record or trial, sire, and
  the profile's own words — shows on the Briefing (a runner's detail) and
  Race Day (the detail panel) for a horse's first five HK starts, with its
  agent as a chip on the row. `background.js`, `background.css`.
- **The background record** below the Briefing's sources' record:
  `GET /api/background/record` (`query/background`), per agent, country,
  import type, sale price and trial, for each era apart.
- **Not in the Screen.** A factor needs walk-forward over seasons that have
  the data; there is one. Revisit after 2026/27.

## The profile's trip verdict (16 Nov 2026)

The profile closes with how far the contractor thinks the horse should get.
`derive/distance_verdict` reads that sentence by rule into the longest trip it
allows:

- **stayer:** 1800 m and over, or "mile or further"
- **miler:** 1500-1799 m, or "1400M or upper"
- **sprinter:** 1400 m and under

It skips clauses about the dam, the sire's other runners or a race already run.
528 of the 617 profiles stored on 16 Nov name a trip.

- **What it showed, on every run to 16 Nov:** stayers at 1600 m and over won
  41 of 390 runs where the closing price expected 28.9 (A/E 1.42). That holds
  whatever the sire. To place they returned $0.75 on the dollar, which is
  ordinary. Found while answering Brett on pedigree
  (`Claude outputs/pedigree-research-2026-11-16.md`).
- **The chip:** `PROFILE STAYER / MILER / SPRINTER` sits on a runner's row
  on Race Day and the Briefing, at any start, where today's trip bears on it:
  1600 m and over, or further than the horse has run here. The contractor's
  sentence is in its tooltip (`query/background.distance_verdicts`,
  `background.js`).
- **The record:** the Briefing's background record has a PROFILE TRIP VERDICT
  table below it. It shows each verdict at 1600 m+ and under, with win A/E and
  $1 to place. Unlike the agent record, it is split by **run date**: the idea
  was formed on every run to 16 Nov, so the runs from 17 Nov are the test,
  whoever the horse.
- **When HK form takes over** is left to the test. Each 1600 m+ line splits
  into the horse's first time at 1600 m+ here and runs after it has been that
  far. To 16 Nov, stayers on a first try won 5 where the price expected 3.9,
  and stayers that had already run there won 36 where it expected 25.0. Both
  are too few to set a cut-off, so the chip shows at any start.
- **Not in the Screen or the model** until the test has something to say.

## The sire's paragraph (fixed 16 Nov 2026)

The comments section usually opens with a paragraph on the SIRE's career. The
reader kept every paragraph containing the horse's first word anywhere, so it
also kept the sire's whenever the two shared that word, or the word sat inside
another one. 51 of 617 stored profiles were affected, 45 of them with an
overseas record that was the sire's:

- BLUE ILLUSION took BLUE POINT's 11 wins from 20 and Charlie Appleby.
- JUSTIFY YOURSELF, never raced, took Justify's 6 from 6 and Bob Baffert.
- EVER LUCK and CAP LINER were credited to James Harron Bloodstock, who owned
  their sire Capitalist.

Only one trip reading was affected (BLUE LIGHTNING). `ingest/newhorse._about`
now skips the paragraph that opens on the sire's name and matches the horse's
name as whole words. Checked on 113 live pages: all 51 affected changed, and
61 of the other 62 read exactly as before. The other one, OUR BOLD CHOICE,
had the same fault and had not been stored yet.

**The stored rows stay wrong until they are read again.** After deploying,
run this once on the server (about 720 pages, roughly 15 minutes at the
courtesy rate):

    nice -n 19 python -m hkrd.jobs.scrape_background --refresh
