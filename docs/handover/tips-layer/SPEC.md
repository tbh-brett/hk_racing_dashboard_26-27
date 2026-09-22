# SPEC — the tips layer

Companion to `README.md`. This is the buildable detail: schema, module map, the
payload contract, resolution rules, endpoints, panels, phases.

---

## 1. Schema — three new tables

All raw. All new tables rather than columns, for the reason in §4.

```sql
-- ── English ↔ Chinese horse names ───────────────────────────────────────────
--
-- LOAD-BEARING, not decorative. 賽馬Fact Check's transcripts name horses in
-- Chinese and give no numbers at all, so without this map there is no path
-- from that source to `runners`. The legacy `horsename_zh` column died in May
-- (see AGENTS.md, Data rules) and nothing replaced it.
--
-- horse_name is the English name, which is the join key everywhere else in
-- this database -- AGENTS.md: "Join history on horse_name, never horse_id."
CREATE TABLE IF NOT EXISTS horse_name_zh (
  horse_name TEXT PRIMARY KEY,
  name_zh    TEXT NOT NULL,
  brand_no   TEXT,
  source     TEXT NOT NULL,        -- racecard_zh | odds_changes | manual
  seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hnz_zh ON horse_name_zh(name_zh);

-- ── what the connections said ───────────────────────────────────────────────
--
-- Its own table rather than a row alongside tipster opinion, because a trainer
-- is not a tipster and the difference is the point: he has information nobody
-- else has and an incentive to be optimistic, so "he might find it a little bit
-- tough" from the man who trains the horse is a far stronger signal than the
-- same sentence from a columnist. Filed together they average each other out,
-- which is the one thing that must not happen.
CREATE TABLE IF NOT EXISTS connections_quote (
  quote_id     TEXT    PRIMARY KEY,   -- '<video_id>:<int(t_start)>'
  race_date    TEXT    NOT NULL,
  race_no      INTEGER,               -- NULL when unresolved; see §5
  horse_no     INTEGER,               -- NULL when unresolved
  horse_said   TEXT,                  -- the name as the source gave it
  speaker      TEXT,                  -- 'Frankie Lor', '潘頓'
  role         TEXT    NOT NULL,      -- trainer | jockey | presenter | analyst
  quote        TEXT    NOT NULL,      -- verbatim, in its original language
  quote_en     TEXT,                  -- translation when the original is Chinese
  topic        TEXT,                  -- fitness|draw|plan|trial|class|doubt|gear
  stance       TEXT,                  -- positive | negative | neutral
  source       TEXT    NOT NULL,      -- rtw_interview | rtw_preview | factcheck | bryan
  video_id     TEXT,
  t_start      REAL,                  -- seconds, so the link jumps to the answer
  caption_kind TEXT,                  -- manual | asr  -- quality tier, see §6
  confidence   REAL,                  -- resolver's own 0..1
  extracted_by TEXT    NOT NULL,      -- 'llm:<model>@<prompt_version>' | 'rule:<name>'
  url          TEXT    NOT NULL,
  fetched_at   TEXT    NOT NULL,
  FOREIGN KEY (race_date) REFERENCES races(race_date)
);
CREATE INDEX IF NOT EXISTS ix_cq_runner
  ON connections_quote(race_date, race_no, horse_no);

-- ── who tipped what ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tipster_selection (
  source       TEXT    NOT NULL,   -- oncc | stheadline | threads | factcheck
  tipster      TEXT    NOT NULL,   -- 西門獨 | 諸葛數 | 王子 | 分析師 | 譚朗蔚
  race_date    TEXT    NOT NULL,
  race_no      INTEGER NOT NULL,
  horse_no     INTEGER NOT NULL,
  pick_rank    INTEGER,            -- 1 = top pick, 2..4 after it; NULL if unranked
  note         TEXT,               -- the tipster's own words, where there are any
  name_seen    TEXT,               -- published name, kept as a checksum only
  url          TEXT,
  published_at TEXT,
  fetched_at   TEXT    NOT NULL,
  PRIMARY KEY (source, tipster, race_date, race_no, horse_no),
  FOREIGN KEY (race_date, race_no) REFERENCES races(race_date, race_no)
);

-- ── what did not resolve, and why ───────────────────────────────────────────
--
-- Read as a count on the ops page. A climbing count is a layout change or a
-- caption change announcing itself. A silent best guess is a wrong horse with
-- a trainer's quote under it, which is the failure this table exists to stop.
CREATE TABLE IF NOT EXISTS tips_quarantine (
  quarantine_id TEXT PRIMARY KEY,   -- sha1 of source + raw, so a re-push dedups
  source        TEXT NOT NULL,
  race_date     TEXT,
  race_no       INTEGER,
  raw           TEXT NOT NULL,
  reason        TEXT NOT NULL,      -- no_runner | no_race | name_unknown
                                    -- | name_mismatch | low_confidence | unparsed
  url           TEXT,
  fetched_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tq_date ON tips_quarantine(race_date, source);
```

`connections_quote.race_no` and `horse_no` are **nullable on purpose**. A quote
that cannot be pinned to a runner is still worth showing at race level, and forcing
a guess into those columns to satisfy a NOT NULL is exactly how a wrong horse ends
up carrying a trainer's endorsement.

---

## 2. Module map

```
hkrd/ingest/tips_payload.py   NEW  validate + normalise a posted payload → dicts   ~150
hkrd/ingest/racecard_zh.py    NEW  the zh-hk card → {horse_no, name_zh}            ~110
hkrd/store/tips.py            NEW  the write path for all three tables             ~170
hkrd/jobs/import_tips.py      NEW  read payload → resolve → store → report         ~160
hkrd/jobs/sync_horse_names.py NEW  zh-hk card → horse_name_zh                      ~90
hkrd/query/tips.py            NEW  card digest + per-race table                    ~200
hkrd/api/routes/tips.py       NEW  1 POST + 2 GET                                  ~130
web/assets/tips.js            NEW  the runner panel + the race table               ~280
hkrd/store/schema.sql          +   3 tables (§1)
hkrd/api/app.py                +   routes.tips in the router tuple (1 line)
hkrd/query/freshness.py        +   one SOURCES entry, `normal` in days not minutes
ops/crontab                    +   one line for sync_horse_names
tools/extract_tips.py         NEW  PC-side: transcripts+posts → payload (§7)
tools/push_tips.py            NEW  PC-side: POST the payload (§7)
```

`ingest/tips_payload.py` belongs in `ingest/` on the house definition — it parses
external input into plain dicts and does not know the database exists. Type
coercion happens at write time in `store/`, per `AGENTS.md`, not here.

---

## 3. The payload contract

One JSON object per meeting. See `payload.example.json` for a filled-in version
built from real transcript text.

```
{
  "payload_version": 1,
  "race_date":    "2026-09-23",          required, YYYY-MM-DD
  "generated_at": "<ISO8601>",
  "extractor":    "llm:claude-opus-5@tips-v1",
  "quotes":      [ ... ],                → connections_quote
  "selections":  [ ... ],                → tipster_selection
  "quarantine":  [ ... ]                 → tips_quarantine
}
```

Rules the validator enforces, and rejects the whole payload on:

- `payload_version` is 1. An unknown version is a hard reject, not a best effort.
- `race_date` parses, and `races` has at least one row for it. A payload for a
  meeting the database has never scraped is a mistake, not data.
- Every `quotes[]` has `source`, `role`, `quote`, `url`, `extracted_by`.
- Every `selections[]` has `source`, `tipster`, `race_no`, `horse_no`.
- `pick_rank`, when present, is 1..8.
- `confidence`, when present, is 0..1.

Rules applied per row, which quarantine that row only:

- `race_no` must exist on that date, else `reason: no_race`.
- `horse_no` must exist in that race, else `reason: no_runner`.
- When `name_seen` is given and `horse_name_zh` knows a different English name for
  it than the runner at that number, `reason: name_mismatch`.
- `confidence < 0.55` on a quote, `reason: low_confidence`.

**Idempotence.** `connections_quote` keys on `video_id:int(t_start)`,
`tipster_selection` on its five-column primary key, `tips_quarantine` on a hash of
source+raw. Every write is `INSERT ... ON CONFLICT DO UPDATE`. Re-pushing the same
payload must change nothing — there is an acceptance test for this.

---

## 4. Why new tables rather than columns

Not because columns cannot reach production — they can, `store/connect._migrate`
is a real migration runner and `api/app._warm` runs it at boot. The older specs in
this project say otherwise and are stale on that point.

The reason is what the tables hold. `runners` is scraped truth about what happened
in a race. A tipster's opinion and a trainer's pre-race quote are neither scraped
truth nor about what happened. Widening `runners` with them would put third-party
forecasts inside the table every derived figure in the system is rebuilt from.

---

## 5. Resolution rules, per source

The rule that holds across all of them: **the transcript never picks the horse.**
Something deterministic narrows the candidate set first, and the choice is then
among a closed list.

| Source | Narrowing step | Then |
|---|---|---|
| on.cc, 星島 | none needed — number published | exact PK lookup; `name_seen` is the checksum |
| Threads | none needed — `R10 10 金勝名駒` | exact PK lookup; `author_tag` is the tipster, not `horsedetective` |
| **賽馬Fact Check** | **`name_zh` → `horse_name_zh` → `horse_name`** | exact lookup in `runners` for that date. **No LLM needed for identity** — the subtitles are human-written, so the name is right. The LLM only summarises the claim and sets `topic`/`stance`. |
| RTW preview | chapter map in the description (`[00:19:56] - Race 7`) gives `race_no` exactly | LLM picks 1-of-10-to-14 by mangled English name |
| RTW interview | the interviewer names the trainer ("David…", "Frankie…") → that trainer's runners that day, usually 3–4 | LLM picks among those |
| 全方位Bryan | nothing reliable | fuzzy match `name_zh` against the meeting's runners; cap `confidence` at 0.6 and quarantine below 0.55 |

**The Fact Check row is the important one.** With correct Chinese names the hardest
source to resolve becomes the easiest — an index lookup — provided `horse_name_zh`
is populated. Hence phase 1.

### Building `horse_name_zh`

`hkrd/ingest/racecard.py` already fetches `urls.racecard` with the `en-us` prefix
and parses the runner table. The Chinese site mirrors it at `zh-hk`, so
`ingest/racecard_zh.py` is the same parse against a sibling URL, at the same 1.2s
throttle, returning `{horse_no, name_zh}` for a race. Joined to the English card on
`(race_date, race_no, horse_no)` it yields English↔Chinese pairs.

**Unverified, and the first thing to check:** that the `zh-hk` card renders the
Chinese names in a parseable table. When this was written the next card was not yet
published and the page showed only 「排位表尚未出版」. Cards publish at noon on
Monday (for Wednesday) and Thursday (for the weekend). If the table turns out not
to be there, the fallback is `ingest/odds.py`'s GraphQL `changeHistories`, which
already returns `horseName_ch` beside `horseName_en` for any horse with a change —
those pairs are free today and being discarded.

Run it from `jobs/sync_horse_names.py` on the same cron cadence as the card scrape.
The table only grows; names never change.

---

## 6. Quality tiers — do not average them

Three tiers, and they must stay distinguishable on the row and on the screen:

| `caption_kind` / source | Trust | Consequence |
|---|---|---|
| `manual` (Fact Check zh-HK) | names correct | may produce a `selection` row |
| `asr` English (RTW) | names mangled, analysis intact | quotes only, `confidence` from the resolver |
| `asr` Cantonese (Bryan, Fact Check tail) | names badly mangled | quotes only, cap confidence, quarantine freely |

`harvest_youtube.py` already tags ASR-filled segments `src: "asr"` when it tops up
a short manual track, and reports `caption_coverage`. Carry that through to
`connections_quote.caption_kind`. A segment that came from the ASR fill must never
produce a `tipster_selection`.

---

## 7. The PC-side tools

Outside `hkrd/`, so the package's import rules and the 600-line cap do not apply.
They may use the Anthropic SDK and anything else they need.

### `tools/extract_tips.py`

```
usage: extract_tips.py --race-date 2026-09-23 --raw ./raw --out ./out [--dry-run]
```

1. Find every raw artefact for that meeting: `raw/*/**.json` transcripts whose
   parsed title date matches, `raw/threads/posts.jsonl` entries in the window,
   `raw/oncc/<YYYYMMDD>/`.
2. Fetch the card for that date from the dashboard —
   `GET /api/raceday/card/{date}` or equivalent — so the closed candidate set is
   the real one. **This is what makes the resolution safe; do not skip it and let
   the model free-associate.**
3. Resolve per §5. Deterministic paths first; call the model only where the table
   says to.
4. Write `out/<race_date>.tips.json` in the §3 shape.
5. `--dry-run` prints the resolution table — source, raw name, resolved runner,
   confidence — and writes nothing. Use it on one meeting before trusting a
   season.

The model prompt must be given the candidate list and told to return a runner from
it or `null`. It must never be asked to name a horse from memory.

### `tools/push_tips.py`

```
usage: push_tips.py out/2026-09-23.tips.json --base https://hkrd.fly.dev
```

POSTs the file. Auth is the existing shared password from `hkrd/api/auth.py` —
read it from an env var, never a flag, and never log it. Print the counts the
endpoint returns.

---

## 8. Endpoints

```
POST /api/tips/import
     body: the §3 payload
     → {"quotes": n, "selections": n, "quarantined": n, "race_date": "..."}
     Calls jobs.import_tips. The ONLY write path, and it goes through a job
     because AGENTS.md allows api/ → jobs/ and nothing else.

GET  /api/tips/race/{race_date}/{race_no}
     → {"runners": {"<horse_no>": {"quotes": [...], "tipsters": [...]}},
        "tipsters_seen": ["西門獨", ...]}
     Everything one race's panel and table need, in one request.

GET  /api/tips/card/{race_date}
     → per-race counts only, for the card page to know which races have anything.
     Deliberately thin: a whole meeting's quotes is not a payload the landing
     page should carry.
```

Route safety: existing `/api/tips/...` paths do not exist, and there is no
`/api/tips/{param}` to shadow a literal. Register `routes.tips` in the `app.py`
tuple.

---

## 9. What lands on the card

`query/raceday.build_card` already hangs `blackbook` off every runner. Both objects
hang off the same place. Folded shut by default — a 14-runner card with three
quotes each is 42 opinions, and a screen showing all of them at once is a screen
you stop reading by the third meeting.

```
 7  BEAUTY ETERNAL                                      3.4  ★★
    ───────────────────────────────────────────────────────────
    連繫  Frankie Lor · trainer · manual              ▸ 2:14
          「第二次試閘好睇啲…今次抽12檔，我哋跳閘之後
            會慢慢嚟，等佢放鬆」
          the second trial looked better … drawn 12, we just
          jump and let him relax
    貼士  西門獨 ①   諸葛數 ③   王子   譚朗蔚 ①
```

- The `▸ 2:14` is `https://www.youtube.com/watch?v={video_id}&t={int(t_start)}s`.
  One click to the trainer actually saying it, which is the only real defence
  against a transcript you half trust. **Always render it when `t_start` is
  present.**
- Chinese quotes show the original first and the translation under it, not instead
  of it. The original is the evidence.
- A quote whose `caption_kind` is `asr` gets a quiet marker, so the eye knows the
  names in it may be wrong.
- Tipster chips carry `pick_rank` where the source published one; hover shows
  `note`.
- **Nothing here reorders, ranks or recolours a runner.** See README, "What NOT to
  do".

Per race, one table — the same data transposed:

| # | Horse | 西門獨 | 諸葛數 | 陳志懷 | 明治朗 | 兆文 | 王子 | 分析師 |
|---|---|---|---|---|---|---|---|---|
| 3 | … | ① | ② | | ① | | ✓ | |
| 7 | … | | ③ | ① | | ② | ✓ | ✓ |

New asset — `web/assets/race-day.js` is 1,588 lines against a 600-line cap.

---

## 10. Phases

| # | Phase | Why this order |
|---|---|---|
| 1 | `horse_name_zh` + `ingest/racecard_zh.py` + `jobs/sync_horse_names.py` | load-bearing for the best source; everything Chinese waits on it |
| 2 | Schema, `store/tips.py`, `ingest/tips_payload.py`, `jobs/import_tips.py`, `POST /api/tips/import` | the whole Fly-side spine, testable against the fixture with no scraping at all |
| 3 | `tools/extract_tips.py` for Fact Check only — the deterministic path | proves the round trip end to end on the cleanest source |
| 4 | `query/tips.py`, the two GETs, `web/assets/tips.js` | the thing Brett actually looks at |
| 5 | Extend the extractor to RTW interviews, then RTW previews | LLM resolution, hardest value first |
| 6 | on.cc and Threads parsers → `selections` | fills the tipster table |
| 7 | Bryan | measure before building |

Phase 2 is fully testable from `payload.example.json` without any network access,
which is why it comes before anything that scrapes.

---

## 11. Open questions for Brett — ask, do not assume

1. **Which model for extraction, and is the Anthropic SDK in `tools/` acceptable?**
   It is outside `hkrd/` so `AGENTS.md` does not forbid it, but he asked to be
   consulted on new dependencies and this is the one that counts.
2. **Does the `zh-hk` race card carry a parseable Chinese name table?** §5. Check
   after noon on a Monday or Thursday. It decides whether phase 1 is half a day or
   a day and a half.
3. **How much of an RTW interview is about today's runners?** A chunk of each is
   season retrospective — "you're sitting on 35 wins, how do you assess the
   campaign?" Measure on five before writing the phase-5 prompt.
4. **Does he want the uncaptioned Fact Check tail at all?** It holds 譚朗蔚's
   selections but only via Cantonese ASR. Cheapest honest answer is to store the
   quotes and skip the selections.
