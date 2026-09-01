"""A window narrows EVERY figure on a page, or it should not be offered.

The failure this guards is subtle and looks fine: the headline respects the
chosen period and the seven panels below it do not, so a reader compares a
month's ROI against a lifetime strike rate without anything on screen saying
they are different windows.
"""
from __future__ import annotations

import pytest

from hkrd.query import bet_analysis as ba, bets as bq, period
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

OLD, RECENT = "2025-11-05", "2026-07-15"


@pytest.fixture()
def db(tmp_path):
    conn = get_conn(tmp_path / "w.db")
    init_db(conn)
    with transaction(conn):
        for d in (OLD, RECENT):
            upsert.upsert_races(conn, [
                {"race_date": d, "race_no": 1, "venue": "HV", "course": "C",
                 "surface": "Turf", "going": "G", "distance": 1650}])
            upsert.upsert_runners(conn, [
                {"race_date": d, "race_no": 1, "horse_no": n,
                 "horse_name": f"H{n}", "draw": n, "place": str(n),
                 "win_odds": 4.0 + n} for n in range(1, 5)])
        # Two bets in the old season, one in the recent window.
        for i, (bet_id, date, ret) in enumerate((
                ("old1", OLD, 0.0), ("old2", OLD, 500.0), ("new1", RECENT, 0.0))):
            conn.execute(
                "INSERT INTO bets (bet_id, account, race_date, race_no, "
                "bet_type, stake, returned, pnl, status, hit, placed_at, "
                "source) VALUES (?,'brett',?,1,'WIN',100,?,?, 'settled',?,?,"
                "'manual')", (bet_id, date, ret, ret - 100,
                              1 if ret else 0, f"{date}T11:00:00"))
            conn.execute(
                "INSERT INTO bet_selections (bet_id, race_no, horse_no, "
                "leg_no, is_banker) VALUES (?,1,?,0,0)", (bet_id, i + 1))
    yield conn
    conn.close()


def test_the_ledger_respects_the_window(db) -> None:
    win = period.resolve("day", anchor=RECENT)
    assert len(bq.ledger(window=win, conn=db)) == 1
    assert len(bq.ledger(conn=db)) == 3


def test_the_summary_respects_the_window(db) -> None:
    win = period.resolve("day", anchor=RECENT)
    assert bq.summary(window=win, conn=db)["bets"] == 1
    assert bq.summary(conn=db)["bets"] == 3


def test_every_analysis_panel_is_measured_over_the_same_window(db) -> None:
    """The whole point. A page whose headline says "this month" while the
    panels below it say "all time" is worse than one with no period at all."""
    win = period.resolve("day", anchor=RECENT)
    out = ba.analysis(window=win, conn=db)
    assert out["overall"]["bets"] == 1
    # Each panel derives from the same row set, so none of them may see the
    # two older bets.
    assert sum(d["bets"] for d in out["by_type"]) == 1
    assert out["cumulative"]["meetings"] == 1
    assert out["cumulative"]["bets"] == 1


def test_the_window_travels_with_the_answer(db) -> None:
    """A figure with no stated window cannot be checked later."""
    out = ba.analysis(window=period.resolve("season", anchor=RECENT), conn=db)
    assert out["window"]["label"] == "SEASON 2025/26"
    assert out["window"]["since"] == "2025-09-01"


def test_a_season_window_keeps_bets_from_the_previous_calendar_year(db) -> None:
    """November 2025 and July 2026 are the SAME season. A calendar year would
    have dropped the November bets from a figure asked for as "this season"."""
    out = ba.analysis(window=period.resolve("season", anchor=RECENT), conn=db)
    assert out["overall"]["bets"] == 3


def test_no_window_still_means_everything(db) -> None:
    """The default must not quietly become a window."""
    assert ba.analysis(conn=db)["overall"]["bets"] == 3
    assert ba.analysis(conn=db)["window"]["period"] == "lifetime"


def test_the_account_and_the_window_narrow_together(db) -> None:
    win = period.resolve("season", anchor=RECENT)
    assert ba.analysis(account="brett", window=win, conn=db)["overall"]["bets"] == 3
    assert ba.analysis(account="kelvin", window=win, conn=db)["overall"]["bets"] == 0


# ── the rule, enforced structurally ──────────────────────────────────────────

def test_no_windowed_function_runs_unfiltered_sql() -> None:
    """A panel that takes a window and then ignores it is invisible.

    Three of them did. `cumulative_pnl`, `favourite_split` and `clv` aggregate
    in SQL rather than through `_ledger_rows`, so threading the parameter
    through their signatures changed nothing and the page went on showing the
    whole archive under a heading that said "this month". Every figure looked
    plausible.

    So the rule is checked on the source: a function that accepts a window
    either reads through the shared loader or applies `period.clause` itself.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "hkrd" / "query"
    offenders = []
    for f in sorted(src.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        for fn in ast.parse(text).body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if not any(a.arg == "window" for a in fn.args.kwonlyargs):
                continue
            body = ast.get_source_segment(text, fn) or ""
            if "conn.execute" not in body:
                continue
            if ("period.clause" in body or "period.named_clause" in body
                    or "_ledger_rows(" in body):
                continue
            # Delegation counts: a function whose own SQL only enumerates
            # (which tags exist) and which hands the window to something that
            # measures is doing the right thing. `backed_and_missed_by_tag`
            # lists the vocabulary and passes the window down per tag, and a
            # tag with no runs in the window should read "no runs" rather than
            # vanish from the table.
            if "window=window" in body:
                continue
            offenders.append(f"{f.name}:{fn.name}")
    assert not offenders, (
        "these take a window and never apply it:\n"
        + "\n".join(f"  · {o}" for o in offenders)
        + "\n\nRead through the shared row loader, or apply period.clause "
          "(or named_clause where the query binds by name).")


def test_clv_counts_its_denominator_over_the_same_window(db) -> None:
    """"n of N selections" with the numerator windowed and N over all time is
    a share of the wrong thing."""
    win = period.resolve("day", anchor=RECENT)
    out = ba.clv(window=win, conn=db)
    assert out["of_selections"] == 1
    assert ba.clv(conn=db)["of_selections"] == 3
