# Handover — the tips layer

**For:** Claude Code, working in `hk_racing_dashboard_26-27`
**Date:** 2026-09-22
**Read order:** this file → `SPEC.md` → `payload.example.json`. `PROMPT.md` is the
brief Brett pastes to start you off; it says the same thing in fewer words.

---

## What this builds

Two things on the race card, and nothing else:

1. **What the connections said about this runner** — the trainer's or jockey's own
   words, with a timestamped link back to them saying it.
2. **Which tipsters picked this runner, and where it sat in their order.**

No consensus score, no divergence-from-market overlay, no leaderboard. The
leaderboard falls out of `tipster_selection` joined to results later for free, so
nothing here forecloses it — but it is not in scope.

---

## The one architectural decision, and why

**Transcript fetching cannot run on Fly.io.** YouTube blocks most datacenter IP
ranges; this was the single most reported transcript-library failure of 2026 and it
covers Fly, AWS, GCP, Azure, Render and Vercel. Tested 2026-09-21: the route that
works needs a residential IP.

**The LLM extraction is a new dependency**, and `AGENTS.md` says no new
dependencies without asking.

Both problems dissolve at the same seam. The work splits at the network boundary:

```
BRETT'S PC — tools/, outside the hkrd package, no house rules
  harvest_youtube.py   →  raw/<src>/<video_id>.json      transcripts (residential IP)
  harvest_threads.py   →  raw/threads/posts.jsonl        Threads posts
  harvest_oncc.py      →  raw/oncc/<YYYYMMDD>/*.html     tipster tables
  extract_tips.py      →  out/<race_date>.tips.json      LLM resolution → rows
  push_tips.py         →  POST /api/tips/import          rows only

FLY — hkrd/, house rules apply, GAINS NO NEW DEPENDENCY
  ingest/tips_payload.py   validate posted JSON → plain dicts
  store/tips.py            upsert
  jobs/import_tips.py      what the router calls
  api/routes/tips.py       POST import · GET card · GET race
  query/tips.py            reads for the two panels
  web/assets/tips.js       the panels
  store/schema.sql         + 3 tables
```

**What you are building is the FLY half.** The PC half already exists as three
tested harvesters in `tools/`; `extract_tips.py` and `push_tips.py` are specified
in `SPEC.md` §7 and are also yours to write, but they live in `tools/` and the
package's import rules do not reach them.

Three things this split buys, all of which matter:

- **The Fly app takes on nothing new.** No LLM SDK, no `yt-dlp`, no
  `youtube-transcript-api`. One router, one job, one store module, three tables.
- **`api/ → jobs/` is the documented write path** out of a router, so the import
  endpoint is in house style rather than an exception to it.
- **No whole-database overwrite.** `ops/install-db.sh` renames an uploaded file
  over the live one and destroys everything production wrote since the last push;
  the gap audit already flagged that as a one-way door. The payload here is rows,
  not a database, every write is `ON CONFLICT DO UPDATE`, and a re-push is
  therefore safe and idempotent.

Raw transcripts stay on the PC, so re-extracting with a better prompt costs no
re-fetching.

---

## The five sources, and what each one actually gives you

All of this was tested live on 2026-09-21 and 2026-09-22, not inferred. The
differences between them drive the resolution rules in `SPEC.md` §5, so it is worth
reading the table before the spec.

| Source | Language | Captions | Race no.? | Horse no.? | Names correct? | Resolution |
|---|---|---|---|---|---|---|
| **賽馬Fact Check** `UCNpEBQatm4NALFlS-HO1nzg` | Chinese | **zh-HK MANUAL** | no | no | **yes** | ZH name → `horse_name` bridge → exact |
| **RTW Race previews** | English | en ASR | **yes, chaptered** | no | mangled | chapter → race → 1-of-14 by LLM |
| **RTW Interviews** | English | en ASR | no | no | mangled | trainer → his runners → LLM |
| **on.cc** | Chinese | — (HTML) | **yes** | **yes** | yes | exact, by number |
| **Threads** `@horsedetective` | Chinese | — (RSS) | **yes** | **yes** | yes | exact, by number |
| **全方位Bryan** `UCQAbEL38om9qqgVHGuK5BSg` | Cantonese | yue ASR | 3 per video | no | **badly mangled** | fuzzy, low confidence |

### 賽馬Fact Check is the best source and it changes one design decision

Its videos carry a **human-written `zh-HK` subtitle track** — the channel flags this
in its own titles as `CC中文字幕` — sitting next to the `yue` auto track. That makes
it a different class of data from everything else: every horse, jockey and trainer
name is spelled correctly, which is precisely what ASR destroys.

Sample, verbatim from the 9.23 Happy Valley preview:

> 富心星四場頭馬全部係谷草短途 包括一場一千米同三場千二米 原本星期日係跑六戰全負嘅田草
> 依家拎住優先出賽權 轉跑最拿手嘅谷草千二 爭勝機會簡直係差天共地

(All four of its wins are at Happy Valley sprints — one at 1000m and three at
1200m. It was going to run on Sha Tin turf, where it is 0 from 6. With a priority
entry it now switches to its best trip; the difference in its chance is night and
day.)

That is per-runner reasoning with correct identities, published about two days
before the meeting.

**The design consequence:** in the earlier spec the English↔Chinese name map was a
*checksum*. For this source it is the **load-bearing join** — the transcript names
horses in Chinese and gives no numbers at all, so without `horse_name` there is no
way to reach `runners`. Build the bridge first. It is phase 1 for that reason.

### Two honest limits on it

- **Caption coverage is partial.** Measured on the 9.23 preview: the manual track
  ends at 370s of 601s, 62%. The uncaptioned tail is where the analyst 譚朗蔚's
  selections sit ("片尾有彩蛋"), so those are *not* in the clean text.
  `harvest_youtube.py` tops the gap up from the ASR track and tags those segments
  `src: "asr"` — treat them as a different quality tier and do not let them
  produce a `selection` row.
- **No race numbers, ever.** Zero matches for `第N場` in the whole transcript. The
  video is organised by horse and stable narrative. Everything hangs off the name.

### 全方位Bryan is real but put it last

Cantonese ASR, and it fails where it hurts: `三in,三in做得旺㗎啦喎` is noise, and
3,116 characters for twelve minutes means the recogniser is dropping a lot — mostly
proper nouns. Three race-number mentions in a whole episode, and the 【大倉猜情尋】
series is organised by stable (第4集告東尼篇 is the Tony Cruz episode) rather than by
race. Harvest it from day one because it is free; resolve it last, quarantine
liberally, and measure the hit rate on one card before building anything on it.

---

## What already exists in the repo, that you should read before writing

- **`AGENTS.md`** — the house rules. Import direction, the 600-line cap, the
  numerical rules. All of them apply to the Fly half.
- **`hkrd/store/connect.py`** — `init_db` applies `schema.sql` and then `_migrate`,
  which is a real migration runner with `PRAGMA table_info` guards. `api/app._warm`
  calls `init_store.run()` on startup. **So new columns DO reach production now** —
  the older specs in this project say otherwise and are stale on that point. New
  tables are still the right shape here, for the reason in `SPEC.md` §4.
- **`hkrd/query/raceday.py`** — `build_card` already hangs the `blackbook` object
  off every runner. Both new objects hang off the same place, the same way.
- **`hkrd/derive/probability.py`** — has `devig` and `actual_over_expected` already,
  for when the leaderboard gets built later. Not needed now.
- **`hkrd/ingest/_client.py`** — the shared session, 1.2s throttle, and the
  `FetchError` / `NotFound` distinction. Nothing in this build fetches from HKJC,
  but the error-handling style is the one to copy.

### Two size constraints to know before you start, not at commit time

- `web/assets/race-day.js` is **1,588 lines** against a 600-line cap. Neither panel
  goes in it. New asset.
- `hkrd/api/app.py` is **593 of 600**. Routers are auto-included from a module
  list, so adding `routes.tips` to that tuple costs one line. Anything more than
  that and the file needs splitting first.

---

## Acceptance criteria

Build is done when all of these hold:

1. `tests/test_smoke.py` passes, including the 600-line cap and the
   import-direction checks.
2. `POST /api/tips/import` with `payload.example.json` returns counts, and a second
   identical POST changes nothing (idempotent).
3. A payload with a `race_no` that has no such race, or a `horse_no` not in that
   race, writes a `tips_quarantine` row and **does not** write a quote or
   selection.
4. `GET /api/tips/race/{date}/{race_no}` answers in under 500 ms on the archive.
5. The card shows, per runner, at most three connections quotes and the tipster
   chips, folded shut by default, and **does not reorder or recolour any runner**.
6. `GET /api/ops/status` reports the quarantine count for the last 7 days.
7. New fixtures in `tests/fixtures/` for the payload and for one real transcript
   JSON, and tests that read them rather than inventing data inline.

## What NOT to do

- Do not reorder, rank or colour runners by tipster consensus. The market has read
  the same tipsters; a consensus selection tool in a pari-mutuel pool is a machine
  for backing short prices. Claims attach to the runner; the card decides the
  order.
- Do not write a best guess into `race_no` / `horse_no` to avoid a NULL. A wrong
  horse carrying a trainer's endorsement is worse than no row.
- Do not put the LLM call, an HTTP client for YouTube, or any new package into
  `hkrd/`. That is the whole point of the split.
- Do not compute a tipster strike rate from Horse Detective's result posts. They
  post winners only; nothing records what lost.
