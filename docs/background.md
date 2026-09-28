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
