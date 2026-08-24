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

CREATE TABLE IF NOT EXISTS trials (
  trial_date        TEXT    NOT NULL,
  trial_no          INTEGER NOT NULL,
  horse_name        TEXT    NOT NULL,
  place             INTEGER,
  finish_time       REAL,
  section_times     TEXT,
  running_positions TEXT,
  venue             TEXT,
  surface           TEXT,
  gear              TEXT,
  comment_text      TEXT,
  PRIMARY KEY (trial_date, trial_no, horse_name)
);

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

CREATE TABLE IF NOT EXISTS runner_tags (
  race_date  TEXT    NOT NULL,
  race_no    INTEGER NOT NULL,
  horse_no   INTEGER NOT NULL,
  tag        TEXT    NOT NULL,
  confidence REAL,
  PRIMARY KEY (race_date, race_no, horse_no, tag)
);

-- ── indexes ──────────────────────────────────────────────────────────────────
-- History is looked up by horse across dates constantly (form guide, lookup,
-- horse page); the meeting index serves race day.

CREATE INDEX IF NOT EXISTS ix_runners_horse ON runners(horse_name, race_date);
CREATE INDEX IF NOT EXISTS ix_runners_date  ON runners(race_date);
CREATE INDEX IF NOT EXISTS ix_snap_race     ON odds_snapshots(race_date, race_no, captured_at);
