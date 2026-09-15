"""jobs/coverage — what the database holds, and which model wrote it.

The survey's first two questions are about rows. This pins the third: a derived
table survives a deploy, because the volume is not rebuilt, so shipping a
changed model leaves every existing row written by the old one and the pages go
on showing them. Rebuilding one archive's `runner_sarr` from `sarr-1.0` to
`sarr-1.1` moved 99.8% of scores, 39.5% of ranks and the top-rated horse in
19.5% of races, and nothing on screen said a word.
"""
from __future__ import annotations

import pytest

from hkrd.jobs import coverage
from hkrd.store.connect import get_conn, init_db, transaction

MEETINGS = ["2026-07-15", "2026-09-06"]


def _db(tmp_path, sarr_versions):
    """sarr_versions: {race_date: derive_version}. Pace is always current."""
    path = tmp_path / "c.db"
    conn = get_conn(path)
    init_db(conn)
    from hkrd.derive import pace as pace_d
    with transaction(conn):
        for date in MEETINGS:
            conn.execute(
                "INSERT INTO races (race_date, race_no, venue, surface, going,"
                " distance) VALUES (?, 1, 'ST', 'Turf', 'G', 1200)", (date,))
            for no in range(1, 7):
                conn.execute(
                    "INSERT INTO runners (race_date, race_no, horse_no,"
                    " horse_name) VALUES (?, 1, ?, ?)", (date, no, f"HORSE {no}"))
                conn.execute(
                    "INSERT INTO runner_pace (race_date, race_no, horse_no,"
                    " derive_version) VALUES (?, 1, ?, ?)",
                    (date, no, pace_d.DERIVE_VERSION))
                if date in sarr_versions:
                    conn.execute(
                        "INSERT INTO runner_sarr (race_date, race_no, horse_no,"
                        " sarr, sarr_rank, n_prior, derive_version)"
                        " VALUES (?, 1, ?, 0.5, ?, 8, ?)",
                        (date, no, no, sarr_versions[date]))
    conn.close()
    return path


# ── the version the code writes is read, never copied ────────────────────────

def test_the_wanted_version_comes_from_the_module_that_stamps_it():
    """Copied into this file it would be a second declaration, and the day the
    model moved it would report every fresh row as stale."""
    from hkrd.derive import et, pace, settle
    from hkrd.model import sarr
    assert coverage.current_versions() == {
        "runner_pace": pace.DERIVE_VERSION,
        "runner_et": et.DERIVE_VERSION,
        "runner_sarr": sarr.DERIVE_VERSION,
        "runner_projection": settle.DERIVE_VERSION,
    }


def test_every_table_it_surveys_actually_carries_the_column():
    """A table listed here without a `derive_version` column would raise on a
    real database and only on a real database."""
    import re
    from pathlib import Path
    sql = Path("hkrd/store/schema.sql").read_text()
    for table, _, _ in coverage.DERIVED:
        body = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\)",
                         sql, re.S)
        assert body, f"{table} is not in the schema"
        assert "derive_version" in body.group(1), table


# ── what it says about a table a generation behind ───────────────────────────

def test_a_current_table_is_not_reported_as_behind(tmp_path):
    from hkrd.model import sarr
    cov = coverage.survey(_db(tmp_path, {d: sarr.DERIVE_VERSION for d in MEETINGS}))
    assert [v for v, _, _ in cov.versions["runner_sarr"]] == [sarr.DERIVE_VERSION]
    assert "BEHIND" not in cov.render()
    assert not [g for g in cov.gaps() if "derive" in g or "still on" in g]


def test_a_stale_table_is_named_with_its_row_count_and_the_wanted_version(tmp_path):
    """The owner's report was "the ratings still look wrong" while every fix
    was committed. This is the line that answers it."""
    from hkrd.model import sarr
    cov = coverage.survey(_db(tmp_path, {d: "sarr-1.0" for d in MEETINGS}))
    out = cov.render()
    assert "sarr-1.0" in out and f"BEHIND — code writes {sarr.DERIVE_VERSION}" in out
    assert f"runner_sarr: 12 rows still on sarr-1.0, " \
           f"code writes {sarr.DERIVE_VERSION}" in cov.gaps()


def test_a_half_rebuilt_table_shows_both_generations(tmp_path):
    """Two meetings rebuilt under a changed model and the rest left alone is
    the case the column exists for, and a single "latest version" row would
    hide it -- the newest rows would read current and the archive would not be
    mentioned."""
    from hkrd.model import sarr
    cov = coverage.survey(_db(tmp_path, {"2026-07-15": "sarr-1.0",
                                         "2026-09-06": sarr.DERIVE_VERSION}))
    present = {v: (n, last) for v, n, last in cov.versions["runner_sarr"]}
    assert present["sarr-1.0"] == (6, "2026-07-15")
    assert present[sarr.DERIVE_VERSION] == (6, "2026-09-06")
    assert "BEHIND" in cov.render()


def test_an_empty_derived_table_is_not_a_stale_one(tmp_path):
    """Nothing derived yet needs a first run, not a rebuild, and the two have
    different remedies."""
    cov = coverage.survey(_db(tmp_path, {}))
    assert cov.versions["runner_sarr"] == []
    out = cov.render()
    assert "nothing derived yet" in out
    assert "BEHIND" not in out


# ── the command it prints has to work where it is printed ────────────────────

def test_the_rebuild_command_names_the_database_that_was_surveyed(tmp_path):
    """Read off the production machine and pasted back, a bare command would
    rebuild whatever `HKRD_DB` points at there -- which is not the database the
    report described."""
    path = _db(tmp_path, {d: "sarr-1.0" for d in MEETINGS})
    out = coverage.survey(path).render()
    assert f"python -m hkrd.jobs.rebuild_sarr --db {path}   # runner_sarr" in out


def test_no_rebuild_command_is_offered_when_nothing_is_behind(tmp_path):
    from hkrd.model import sarr
    out = coverage.survey(
        _db(tmp_path, {d: sarr.DERIVE_VERSION for d in MEETINGS})).render()
    assert "Rebuild with" not in out


@pytest.mark.parametrize("table,job", [(t, j) for t, _, j in coverage.DERIVED])
def test_every_rebuild_command_is_one_the_named_job_would_accept(table, job,
                                                                 capsys):
    """A printed command is an instruction, and this one is read on a machine
    the reader reached through `fly ssh`. A module that was renamed, or a flag
    the job does not take, fails there rather than here."""
    from importlib import import_module
    module, *flags = job.split()
    job_module = import_module(module)
    with pytest.raises(SystemExit) as exit_code:
        job_module.main(["--help"])
    assert exit_code.value.code == 0
    usage = capsys.readouterr().out
    # `--db` is appended to every one of these by `Coverage.db_flag`.
    assert "--db" in usage, f"{module} takes no --db, for {table}"
    for flag in flags:
        assert flag in usage, f"{module} takes no {flag}, for {table}"
