"""Fixed odds, the Racing & Sports tips, and the meeting summary on them.

Fixtures, all real, all 2026-09-23 Happy Valley race 6:
  ladbrokes_20260923_r6.json   the Ladbrokes feed's record for the race
  sportsbet_20260923_r6.json   Sportsbet's race card, trimmed
  tote_20260923_r6.json        the HKJC tote as jobs/scrape_odds captured it
  roster_20260923.json         the card the dashboard served
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hkrd.derive.probability import devig
from hkrd.ingest import ladbrokes, sportsbet
from hkrd.jobs import import_tips, scrape_fixed_odds
from hkrd.query import tips_summary
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-09-23"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def lad() -> dict:
    rec = load("ladbrokes_20260923_r6.json")
    rec["_event_id"] = rec["race"]["event_id"]
    return rec


# ── reading the feed ─────────────────────────────────────────────────────────

def test_the_tips_come_in_ladbrokes_own_order(lad):
    """The order is `race.tips`, data rather than prose."""
    got = ladbrokes.tips(lad)
    assert [(t["rank"], t["horse_no"], t["name"]) for t in got] == [
        (1, 4, "SUPERB KING"), (2, 1, "JUMBO BLESSING"),
        (3, 12, "MAPOGO"), (4, 9, "HARMONY FIRE")]


def test_each_tip_carries_its_own_reasons_and_no_one_elses(lad):
    first = ladbrokes.tips(lad)[0]["reason"]
    assert first.startswith("SUPERB KING (4) produced a terrific first-up")
    assert "JUMBO BLESSING" not in first


def test_prices_are_per_runner_with_number_and_name(lad):
    got = {p["horse_no"]: p for p in ladbrokes.prices(lad)}
    raw = {r["runner_number"]: r["odds"] for r in lad["runners"]}
    assert len(got) == 12
    assert got[1]["name"] == "JUMBO BLESSING"
    assert got[1]["win"] == raw[1]["fixed_win"]


# ── the job ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, lad, monkeypatch) -> Path:
    """Race 6 of the real card stored, the tote captured, and Ladbrokes
    served from the fixture."""
    path = tmp_path / "odds.db"
    field = next(r for r in load("roster_20260923.json")["races"]
                 if r["race_no"] == 6)["runners"]
    tote = load("tote_20260923_r6.json")
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": DATE, "race_no": 6,
                                    "venue": "HV", "distance": 1000}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 6, "horse_no": x["horse_no"],
             "horse_name": x["horse_name"]} for x in field])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": 6, "horse_no": p["horse_no"],
             "captured_at": tote["captured_at"], "win_odds": p["win_odds"],
             "place_odds": p["place_odds"]} for p in tote["prices"]])
    conn.close()
    monkeypatch.setattr(scrape_fixed_odds.ladbrokes, "race_ids",
                        lambda *a, **k: {6: lad["_event_id"]})
    monkeypatch.setattr(scrape_fixed_odds.ladbrokes, "fetch_race",
                        lambda *a, **k: copy.deepcopy(lad))
    return path


def rows(db: Path, sql: str) -> list[tuple]:
    conn = get_conn(db)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_the_job_stores_prices_and_tips(db):
    got = scrape_fixed_odds.scrape(DATE, db=db)
    assert (got.races, got.prices, got.tips, got.tips_held) == (1, 12, 4, 0)
    assert rows(db, "SELECT count(*) FROM fixed_odds") == [(12,)]
    assert rows(db, "SELECT horse_no FROM tipster_selection WHERE "
                    "source = 'racing_sports' ORDER BY pick_rank") == \
        [(4,), (1,), (12,), (9,)]
    # The whole comment, on the race rather than on a horse.
    assert rows(db, "SELECT race_no, horse_no, substr(quote, 1, 14) FROM "
                    "connections_quote WHERE source = 'racing_sports'") == \
        [(6, None, "SUPERB KING (4")]


def test_a_price_under_the_wrong_name_is_not_stored(db, lad, monkeypatch):
    """One horse's odds beside another's tote is the one mistake a price
    comparison cannot survive."""
    bent = copy.deepcopy(lad)
    bent["runners"][0]["name"] = "Superb King"      # runner #1 on the feed
    monkeypatch.setattr(scrape_fixed_odds.ladbrokes, "fetch_race",
                        lambda *a, **k: copy.deepcopy(bent))
    got = scrape_fixed_odds.scrape(DATE, db=db)
    assert got.prices == 11
    assert any("the card has JUMBO BLESSING" in s for s in got.skipped)


def test_running_the_job_twice_leaves_one_set_of_tips(db):
    scrape_fixed_odds.scrape(DATE, db=db)
    scrape_fixed_odds.scrape(DATE, db=db)
    assert rows(db, "SELECT count(*) FROM tipster_selection") == [(4,)]


# ── the summary ──────────────────────────────────────────────────────────────

def test_the_summary_orders_by_support_and_prices_each_pick(db):
    scrape_fixed_odds.scrape(DATE, db=db)
    # A second source picking JUMBO BLESSING on top, and Bryan saying
    # something about HARMONY FIRE — which must not count as support.
    import_tips.run({
        "payload_version": 1, "race_date": DATE,
        "generated_at": "2026-09-23T05:00:00Z",
        "selections": [{"source": "rtw_preview", "tipster": "Paul Lally",
                        "race_no": 6, "horse_no": 1, "pick_rank": 1}],
        "quotes": [{"source": "bryan", "role": "analyst", "race_no": 6,
                    "horse_no": 9, "quote": "…", "url": "https://x",
                    "extracted_by": "rule:bryan-v1"}]}, db=db)
    conn = get_conn(db)
    try:
        race = tips_summary.summary(DATE, conn=conn)["races"][0]
    finally:
        conn.close()
    order = [p["horse_no"] for p in race["picks"]]
    assert order[0] == 1                             # two sources, both #1s
    harmony = next(p for p in race["picks"] if p["horse_no"] == 9)
    assert harmony["supporters"] == 1         # Racing & Sports, not Bryan

    tote = {p["horse_no"]: p["win_odds"] for p in
            load("tote_20260923_r6.json")["prices"]}
    fixed = {r["runner_number"]: r["odds"]["fixed_win"]
             for r in load("ladbrokes_20260923_r6.json")["runners"]}
    numbers = sorted(tote)
    fair = dict(zip(numbers, devig([tote[n] for n in numbers])))
    jumbo = race["picks"][0]["odds"]
    assert jumbo["tote"]["win"] == tote[1]
    assert jumbo["ladbrokes"]["win"] == fixed[1]
    assert jumbo["ladbrokes"]["value_pct"] == pytest.approx(
        100 * (fair[1] * fixed[1] - 1), abs=0.1)


# ── Sportsbet, from the PC ───────────────────────────────────────────────────

@pytest.fixture()
def sb() -> dict:
    return load("sportsbet_20260923_r6.json")["racecardEvent"]


def test_sportsbet_reads_the_fixed_price_not_the_mid_tote(sb):
    got = {p["horse_no"]: p for p in sportsbet.prices(sb)}
    assert len(got) == 12 and got[1]["name"] == "JUMBO BLESSING"
    fixed = next(p for p in sb["markets"][0]["selections"][0]["prices"]
                 if p["priceCode"] == "L")
    assert (got[1]["win"], got[1]["place"]) == (fixed["winPrice"],
                                               fixed["placePrice"])


def test_sportsbet_and_ladbrokes_carry_the_same_racing_and_sports_tips(sb, lad):
    """Same four, same order — which is why they are one source, not two."""
    assert [t["horse_no"] for t in sportsbet.tips(sb)] == \
        [t["horse_no"] for t in ladbrokes.tips(lad)]
    assert sportsbet.comment(sb).split()[:6] == \
        ladbrokes.comment(lad).split()[:6]


def test_every_runner_has_a_racing_and_sports_line(sb):
    lines = sportsbet.runner_comments(sb)
    assert len(lines) == 12
    assert lines[0]["comment"].startswith("Was a first-up winner")


def _sportsbet_payload(sb: dict) -> dict:
    """What tools/harvest_sportsbet sends for this one race."""
    from hkrd.ingest import racing_sports as rs
    url = sportsbet.page_url(sb)
    return {
        "payload_version": 1, "race_date": DATE,
        "generated_at": "2026-09-23T09:20:00Z", "extractor": "rule:sportsbet-v1",
        "sources": [rs.SOURCE, rs.FORM_SOURCE],
        "selections": rs.selections(6, sportsbet.tips(sb), sportsbet.comment(sb),
                                    url),
        "quotes": rs.race_comment(6, sportsbet.comment(sb), url,
                                  extractor="rule:sportsbet-v1")
        + rs.runner_comments(6, sportsbet.runner_comments(sb), url,
                             extractor="rule:sportsbet-v1"),
        "fixed_odds": [{"bookmaker": "sportsbet", "race_no": 6,
                        "horse_no": p["horse_no"], "name_seen": p["name"],
                        "win": p["win"], "place": p["place"],
                        "scratched": p["scratched"],
                        "captured_at": "2026-09-23T09:20:00Z"}
                       for p in sportsbet.prices(sb)]}


def test_a_sportsbet_payload_lands_prices_tips_and_runner_lines(db, sb):
    got = import_tips.run(_sportsbet_payload(sb), db=db)
    assert (got.prices, got.selections, got.quarantined) == (12, 4, 0)
    assert rows(db, "SELECT count(*) FROM connections_quote WHERE "
                    "source = 'racing_sports_form'") == [(12,)]


def test_a_sportsbet_price_under_the_wrong_name_is_not_stored(db, sb):
    body = _sportsbet_payload(sb)
    body["fixed_odds"][0]["name_seen"] = "SUPERB KING"      # runner #1
    got = import_tips.run(body, db=db)
    assert got.prices == 11
    assert any("the card has JUMBO BLESSING" in s for s in got.prices_skipped)


def test_an_unknown_bookmaker_rejects_the_payload(db, sb):
    body = _sportsbet_payload(sb)
    body["fixed_odds"][0]["bookmaker"] = "tab"
    with pytest.raises(import_tips.PayloadError, match="bookmaker"):
        import_tips.run(body, db=db)


def test_both_bookmakers_leave_one_racing_and_sports(db, sb):
    """Ladbrokes' capture and Sportsbet's write the same source: whichever
    came last is what is stored, never both, and the runner lines Ladbrokes
    does not have survive its capture."""
    import_tips.run(_sportsbet_payload(sb), db=db)
    scrape_fixed_odds.scrape(DATE, db=db)
    assert rows(db, "SELECT count(*) FROM tipster_selection") == [(4,)]
    assert rows(db, "SELECT count(*) FROM connections_quote WHERE "
                    "source = 'racing_sports'") == [(1,)]
    assert rows(db, "SELECT count(*) FROM connections_quote WHERE "
                    "source = 'racing_sports_form'") == [(12,)]
    conn = get_conn(db)
    try:
        s = tips_summary.summary(DATE, conn=conn)
    finally:
        conn.close()
    assert s["books"] == ["ladbrokes", "sportsbet"]
    superb = next(p for p in s["races"][0]["picks"] if p["horse_no"] == 4)
    assert superb["supporters"] == 1
    assert superb["form"]["text"]
    assert set(superb["odds"]) >= {"tote", "ladbrokes", "sportsbet"}
    status = {x["source"]: x for x in s["source_status"]}
    assert status["racing_sports"]["runner_lines"] == 12
    assert not status["factcheck"]["published"]


def test_the_scheduled_run_on_a_day_with_no_meeting_is_quiet(tmp_path):
    """The crontab asks about today every half hour; most days have no card."""
    got = scrape_fixed_odds.scrape("2026-09-24", db=tmp_path / "none.db",
                                   idle_if_no_card=True)
    assert got.idle and not got.errors
    named = scrape_fixed_odds.scrape("2026-09-24", db=tmp_path / "none.db")
    assert named.errors and not named.idle
