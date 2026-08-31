-- schema.sql — raw tables are scraped truth, derived tables are rebuildable.
--
-- Raw is never recomputed. Every derived table can be DROPped and rebuilt from
-- raw alone, and carries derive_version so a formula change does not destroy
-- old figures.

PRAGMA foreign_keys = ON;

-- ── raw ──────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS races (
  race_date  TEXT    NOT NULL,          -- YYYY-MM-DD
  race_no    INTEGER NOT NULL,
  venue      TEXT,                      -- ST | HV
  course     TEXT,                      -- A, B, C, C+3, AWT
  surface    TEXT,                      -- Turf | AWT  (never conflate: AWT was
                                         -- mislabelled Turf in the v4 builder)
  going      TEXT,
  distance   INTEGER,
  race_class TEXT,
  race_name  TEXT,
  off_time   TEXT,
  PRIMARY KEY (race_date, race_no)
);

CREATE TABLE IF NOT EXISTS runners (
  race_date         TEXT    NOT NULL,
  race_no           INTEGER NOT NULL,
  horse_no          INTEGER NOT NULL,
  horse_name        TEXT    NOT NULL,   -- the join key. NEVER horse_id.
  place             INTEGER,            -- NULL for WV/UR/PU non-finishers
  place_code        TEXT,               -- the raw token when not a placing
  dead_heat         INTEGER NOT NULL DEFAULT 0,
  finish_time       REAL,               -- seconds
  lengths_behind    REAL,               -- parsed; never pd.to_numeric
  draw              INTEGER,
  jockey            TEXT,
  trainer           TEXT,
  actual_weight     INTEGER,
  declared_weight   INTEGER,
  gear              TEXT,
  rating            INTEGER,
  win_odds          REAL,
  section_times     TEXT,               -- ';'-separated seconds
  running_positions TEXT,               -- space-separated ints
  PRIMARY KEY (race_date, race_no, horse_no),
  FOREIGN KEY (race_date, race_no) REFERENCES races(race_date, race_no)
);

CREATE TABLE IF NOT EXISTS dividends (
  race_date       TEXT    NOT NULL,
  race_no         INTEGER NOT NULL,
  pool            TEXT    NOT NULL,     -- WIN PLACE QIN QPL TRIO TCE FCT QTT F4
  combination     TEXT    NOT NULL,
  dividend_per_10 REAL,
  PRIMARY KEY (race_date, race_no, pool, combination)
);

CREATE TABLE IF NOT EXISTS runner_comments (
  race_date    TEXT    NOT NULL,
  race_no      INTEGER NOT NULL,
  horse_no     INTEGER NOT NULL,
  comment_text TEXT,
  source       TEXT,                    -- 'incident' | 'commentary'
  PRIMARY KEY (race_date, race_no, horse_no, source)
);

-- The highest-value table here. Never deleted, never pruned: odds movement is
-- the only data in this system that cannot be reconstructed after the fact.
CREATE TABLE IF NOT EXISTS odds_snapshots (
  race_date   TEXT    NOT NULL,
  race_no     INTEGER NOT NULL,
  horse_no    INTEGER NOT NULL,
  captured_at TEXT    NOT NULL,         -- ISO8601
  win_odds    REAL,
  place_odds  REAL,
  PRIMARY KEY (race_date, race_no, horse_no, captured_at)
);

-- Quinella and quinella-place matrices, timestamped like the win/place
-- snapshots. Pair ranking is worth about +25 ROI points over taking
-- combinations at random within the pool, so the market's own pair prices are
-- the benchmark any model pair ranking has to be judged against.
CREATE TABLE IF NOT EXISTS odds_pairs (
  race_date   TEXT    NOT NULL,
  race_no     INTEGER NOT NULL,
  pool        TEXT    NOT NULL,     -- QIN | QPL
  horse_a     INTEGER NOT NULL,
  horse_b     INTEGER NOT NULL,     -- always stored with horse_a < horse_b
  captured_at TEXT    NOT NULL,
  odds        REAL,
  PRIMARY KEY (race_date, race_no, pool, horse_a, horse_b, captured_at)
);

CREATE TABLE IF NOT EXISTS trials (
  trial_date        TEXT    NOT NULL,
  trial_no          INTEGER NOT NULL,
  horse_name        TEXT    NOT NULL,
  place             INTEGER,                -- derived: RESULT is empty at
                                            -- source, so it is the LAST
                                            -- running position (question C2)
  finish_time       REAL,
  section_times     TEXT,
  running_positions TEXT,
  venue             TEXT,
  course            TEXT,                   -- the course name as published
  surface           TEXT,
  distance          INTEGER,                -- published in the batch header;
                                            -- the legacy import dropped it, so
                                            -- no archived trial carries one
  going             TEXT,
  jockey            TEXT,
  trainer           TEXT,
  draw              INTEGER,
  lengths_behind    REAL,
  gear              TEXT,
  comment_text      TEXT,
  PRIMARY KEY (trial_date, trial_no, horse_name)
);

-- Veterinary records. One row per NOTE, not per horse: a horse can carry
-- several, and collapsing them to the most recent loses the pattern that makes
-- them worth reading. Nothing is filtered on the way in -- the old scraper
-- scored each note and dropped the ones below a threshold, so a record that
-- existed on the page and did not survive the filter was simply not there.
CREATE TABLE IF NOT EXISTS vet_records (
  race_date    TEXT    NOT NULL,
  race_no      INTEGER NOT NULL,
  horse_no     INTEGER,
  horse_name   TEXT    NOT NULL,
  record_date  TEXT    NOT NULL,
  detail       TEXT    NOT NULL,
  passed_date  TEXT,                        -- cleared to race, when given
  category     TEXT,                        -- RESPIRATORY | CARDIAC | PHYSICAL
                                            -- | PERFORMANCE | PROCEDURAL
                                            -- | UNKNOWN
  PRIMARY KEY (race_date, race_no, horse_name, record_date, detail)
);

CREATE INDEX IF NOT EXISTS ix_vet_horse ON vet_records(horse_name, record_date);

-- ── bets ─────────────────────────────────────────────────────────────────────
--
-- The ledger. Design brief 06 Part 1 calls missed bets "the single most
-- important feature" of the Blackbook: without knowing what was BACKED you
-- only ever see the hits, and the book reads as a scrapbook. That comparison
-- is a join from a blackbook horse to these rows, which is why selections are
-- normalised rather than left as a JSON list.

CREATE TABLE IF NOT EXISTS bets (
  bet_id        TEXT PRIMARY KEY,       -- stable id from the legacy log
  bookie_ref    TEXT,                   -- statement reference, for dedup
  account       TEXT,                   -- personal | joint | client
  race_date     TEXT NOT NULL,
  venue         TEXT,
  race_no       INTEGER,                -- NULL for an all-up, which spans races
  bet_type      TEXT NOT NULL,          -- WIN | PLACE | QIN | QPL | ALLUP_* ...
  all_up_formula TEXT,
  stake         REAL NOT NULL,
  returned      REAL,                   -- NULL while unsettled, 0 when it lost
  pnl           REAL,
  status        TEXT NOT NULL,          -- open | settled | void
  hit           INTEGER,                -- 1 | 0 | NULL while open
  settle_method TEXT,                   -- dividend | bookie_statement
  placed_at     TEXT,
  settled_at    TEXT,
  source        TEXT NOT NULL,          -- legacy_log | statement | manual
  notes         TEXT
);

-- One row per horse backed, per leg. This is the table the Blackbook joins to,
-- so a bet on six horses is six rows rather than a list nothing can query.
CREATE TABLE IF NOT EXISTS bet_selections (
  bet_id    TEXT    NOT NULL,
  race_no   INTEGER NOT NULL,           -- the LEG's race, not the bet's
  horse_no  INTEGER NOT NULL,
  leg_no    INTEGER NOT NULL DEFAULT 0, -- 0 for a single-race bet
  is_banker INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bet_id, race_no, horse_no, leg_no),
  FOREIGN KEY (bet_id) REFERENCES bets(bet_id)
);

CREATE INDEX IF NOT EXISTS ix_bets_date ON bets(race_date, race_no);
CREATE INDEX IF NOT EXISTS ix_bet_sel_race ON bet_selections(race_no, horse_no);

-- One row per bet a statement was actually read for. Without this, a bet whose
-- bookie reference was recovered out of the legacy log's notes is
-- indistinguishable from one a statement confirmed, and the reconciliation
-- reports every bet as reconciled when two statements out of thirty meetings
-- have been read. Design brief: "Nothing is silently merged."
CREATE TABLE IF NOT EXISTS bet_statement_rows (
  bet_id      TEXT NOT NULL,
  bookie_ref  TEXT NOT NULL,
  source_file TEXT NOT NULL,
  stake       REAL,                     -- as the STATEMENT has it
  returned    REAL,                     -- ditto, before any apportioning
  imported_at TEXT NOT NULL,
  PRIMARY KEY (bet_id, source_file),
  FOREIGN KEY (bet_id) REFERENCES bets(bet_id)
);

-- ── blackbook ────────────────────────────────────────────────────────────────
--
-- A hypothesis tracker, not a list of favourites: every entry is a claim that
-- this horse will run better than its public form suggests, for a stated
-- reason. The page's job is to show whether those claims pay off.

CREATE TABLE IF NOT EXISTS blackbook (
  id           TEXT PRIMARY KEY,     -- bb_0001 in the legacy export
  horse_name   TEXT NOT NULL,        -- the join key, as everywhere else
  added_date   TEXT NOT NULL,
  expiry_date  TEXT,
  status       TEXT NOT NULL,        -- active | expired | won_out | retired
  reasoning    TEXT,
  confidence   TEXT,                 -- low | medium | high
  source_race  TEXT,                 -- 'YYYY-MM-DD Rn', the run that prompted it
  source_date  TEXT,
  source_race_no INTEGER,
  -- 'memo' when the user typed a date, 'matched' when it was recovered from the
  -- horse's own runs. The page must be able to tell the two apart.
  source_date_from TEXT,
  pref_distance TEXT,
  pref_surface  TEXT,
  pref_jockey   TEXT
);

CREATE TABLE IF NOT EXISTS blackbook_tags (
  id  TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (id, tag),
  FOREIGN KEY (id) REFERENCES blackbook(id)
);

-- Hand-written observations about a run since booking. Kept separate from the
-- runs themselves, which are derived: 171 of 196 legacy entries had no
-- recorded performance at all, because logging one relied on remembering to.
CREATE TABLE IF NOT EXISTS blackbook_notes (
  id          TEXT    NOT NULL,
  race_date   TEXT    NOT NULL,
  race_no     INTEGER,
  finish      TEXT,
  model_rank  INTEGER,
  verdict     TEXT,                  -- VALIDATED | PARTIAL | MISSED
  notes       TEXT,
  PRIMARY KEY (id, race_date, race_no),
  FOREIGN KEY (id) REFERENCES blackbook(id)
);

-- An observation about ONE run. Design brief 06 Part 0 keeps this distinct from
-- a blackbook entry: a note is a record of what happened, an entry is a
-- judgement that the horse is worth following. Most notes are records, so a
-- note must never auto-create an entry — promotion is a deliberate step.
CREATE TABLE IF NOT EXISTS run_notes (
  horse_name TEXT    NOT NULL,
  race_date  TEXT    NOT NULL,
  race_no    INTEGER NOT NULL,
  note       TEXT    NOT NULL,
  written_at TEXT    NOT NULL,
  PRIMARY KEY (horse_name, race_date, race_no)
);

CREATE TABLE IF NOT EXISTS blackbook_tag_definitions (
  tag        TEXT PRIMARY KEY,
  definition TEXT
);

CREATE INDEX IF NOT EXISTS ix_blackbook_horse ON blackbook(horse_name, status);

-- ── derived ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS runner_pace (
  race_date      TEXT    NOT NULL,
  race_no        INTEGER NOT NULL,
  horse_no       INTEGER NOT NULL,
  sec_400        TEXT,
  early_pace     REAL,
  late_pace      REAL,
  early_dev      REAL,                  -- vs RACE MEDIAN, not a par
  late_dev       REAL,
  ssi            REAL,
  pace_style     TEXT,                  -- Leader|On-Pace|Midfield|Closer
  derive_version TEXT    NOT NULL,
  computed_at    TEXT,
  PRIMARY KEY (race_date, race_no, horse_no)
);

CREATE TABLE IF NOT EXISTS runner_et (
  race_date      TEXT    NOT NULL,
  race_no        INTEGER NOT NULL,
  horse_no       INTEGER NOT NULL,
  et             REAL,
  et_level       TEXT,
  et_n_eff       INTEGER,               -- EFFECTIVE sample size, not cell n
  et_shrunk      INTEGER,
  sec_vs_par     REAL,
  len_vs_par     REAL,
  sec_vs_race    REAL,
  len_vs_race    REAL,
  figure         REAL,
  confidence     TEXT,                  -- low | medium | high
  derive_version TEXT    NOT NULL,
  PRIMARY KEY (race_date, race_no, horse_no)
);

CREATE TABLE IF NOT EXISTS runner_sarr (
  race_date      TEXT    NOT NULL,
  race_no        INTEGER NOT NULL,
  horse_no       INTEGER NOT NULL,
  sarr           REAL,
  sarr_rank      INTEGER,
  n_prior        INTEGER,
  derive_version TEXT    NOT NULL,
  PRIMARY KEY (race_date, race_no, horse_no)
);

-- What the score is made of. Long rather than nine columns, because the term
-- list is a property of the model and not of the table: adding one must not
-- need a migration. Written by the same call that produces the score, so the
-- components on screen always sum to the number beside them.
CREATE TABLE IF NOT EXISTS runner_sarr_component (
  race_date    TEXT    NOT NULL,
  race_no      INTEGER NOT NULL,
  horse_no     INTEGER NOT NULL,
  component    TEXT    NOT NULL,   -- fmrp | lsa | traj | esz | wpr | style | ...
  contribution REAL    NOT NULL,   -- signed, already multiplied by the weight
  PRIMARY KEY (race_date, race_no, horse_no, component),
  FOREIGN KEY (race_date, race_no, horse_no)
    REFERENCES runner_sarr(race_date, race_no, horse_no)
);

CREATE TABLE IF NOT EXISTS runner_tags (
  race_date  TEXT    NOT NULL,
  race_no    INTEGER NOT NULL,
  horse_no   INTEGER NOT NULL,
  tag        TEXT    NOT NULL,
  confidence REAL,
  PRIMARY KEY (race_date, race_no, horse_no, tag)
);

-- ── operations ───────────────────────────────────────────────────────────────

-- What the scheduled jobs did, and when. Not a log file: on a deployed box the
-- question "is the data stale, or did Wednesday's scrape fail?" has to be
-- answerable from the dashboard itself, by the person who owns it, without a
-- terminal. The health endpoint reads this table and the last row is the
-- answer.
--
-- Kept deliberately small. It records the OUTCOME of a run, not its output —
-- the counts live in the tables the run wrote, and duplicating them here would
-- create the second copy of a number this package exists to avoid.
CREATE TABLE IF NOT EXISTS job_runs (
  job         TEXT    NOT NULL,          -- nightly | scrape_meeting | derive_all
  started_at  TEXT    NOT NULL,          -- ISO-8601 UTC
  finished_at TEXT,                      -- NULL while running, or if killed
  ok          INTEGER,                   -- 1 | 0 | NULL if it never finished
  detail      TEXT,                      -- one line, the same the CLI prints
  PRIMARY KEY (job, started_at)
);

CREATE INDEX IF NOT EXISTS ix_job_runs_recent ON job_runs(job, started_at DESC);

-- ── bet entry ────────────────────────────────────────────────────────────────
-- Design brief 06 Part 2 and 07 §3. A guardrail FLAGS, it never blocks, and the
-- override is the useful record: "reviewing which flags were overridden and how
-- those bets performed is a genuine analysis, and it's only possible if the
-- override is logged rather than the bet blocked." So a fired guardrail that the
-- user went past writes a row here, keyed to the bet it was fired against.
CREATE TABLE IF NOT EXISTS bet_overrides (
  bet_id        TEXT    NOT NULL,
  flag          TEXT    NOT NULL,        -- raceday_ceiling | max_combinations | ...
  detail        TEXT,                    -- what the flag said at the time
  overridden_at TEXT    NOT NULL,        -- ISO-8601 UTC
  PRIMARY KEY (bet_id, flag),
  FOREIGN KEY (bet_id) REFERENCES bets(bet_id)
);

-- The thresholds those guardrails read. One row per key so a value can change
-- without a migration, and so the interface can show what it is warning against
-- rather than asserting a number the user never set.
CREATE TABLE IF NOT EXISTS bet_settings (
  key        TEXT PRIMARY KEY,           -- raceday_ceiling | max_combinations
  value      REAL NOT NULL,
  updated_at TEXT
);

-- Which thesis a bet was placed ON, recorded at entry rather than inferred.
-- backed-versus-missed already derives a link structurally, from the selection
-- join: that answers "was this booked horse backed". This answers a different
-- question -- "was this bet placed BECAUSE of that entry" -- and only the user
-- knows it, so it is captured when the ticket is built or not at all.
-- A separate table rather than a column on `bets`: the schema is applied as
-- CREATE ... IF NOT EXISTS, so a new column would never reach a database that
-- already has the table, while a new table does.
CREATE TABLE IF NOT EXISTS bet_blackbook_links (
  bet_id   TEXT NOT NULL,
  entry_id TEXT NOT NULL,
  PRIMARY KEY (bet_id, entry_id),
  FOREIGN KEY (bet_id) REFERENCES bets(bet_id),
  FOREIGN KEY (entry_id) REFERENCES blackbook(id)
);

CREATE INDEX IF NOT EXISTS ix_bets_account_date ON bets(account, race_date);

-- ── indexes ──────────────────────────────────────────────────────────────────
-- History is looked up by horse across dates constantly (form guide, lookup,
-- horse page); the meeting index serves race day.

CREATE INDEX IF NOT EXISTS ix_runners_horse ON runners(horse_name, race_date);
CREATE INDEX IF NOT EXISTS ix_runners_date  ON runners(race_date);
CREATE INDEX IF NOT EXISTS ix_snap_race     ON odds_snapshots(race_date, race_no, captured_at);
