"""Race class: one vocabulary, read from every way HKJC writes it.

27 Sep 2026 R8 is a Group 3 and the card read it as a Group 1 off the site's
navigation menu. 196 archived races had no class at all -- every Group race,
the 4-year-old series and every "(Restricted)" race -- because the results
parser read one of HKJC's five phrasings.
"""
from __future__ import annotations

import pytest

from hkrd.ingest import racecard as rc
from hkrd.ingest import results
from hkrd.ingest.headers import parse_meeting_headers
from hkrd.jobs import repair
from hkrd.store import upsert
from hkrd.store.coerce import RACE_CLASSES, to_race_class
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.mark.parametrize("phrase, expect", [
    ("Class 4", ("4", 0)),
    ("Class 3 (Restricted)", ("3", 1)),
    ("Group Three", ("G3", 0)),
    ("Group 1", ("G1", 0)),
    ("Griffin Race", ("Griffin Race", 1)),
    ("4 Year Olds", ("4YO", 1)),
    # A Classic card may print the grade beside the series; the results page
    # never does, so the series wins and the two pages agree.
    ("Group One 4 Year Olds", ("4YO", 1)),
    ("Listed Race", ("Listed", 0)),
    ("4", ("4", None)),          # legacy: class 4, restricted or not unknown
    ("0", ("0", None)),          # legacy: a Group race of unknown grade
    (None, (None, None)),
    ("--", (None, None)),
])
def test_every_phrasing_lands_in_one_vocabulary(phrase, expect) -> None:
    assert to_race_class(phrase) == expect


def test_every_canonical_class_is_its_own_answer() -> None:
    for c in RACE_CLASSES:
        assert to_race_class(c)[0] == c


def _card(header: str, *, menu: str = "") -> str:
    return (f"<html><body><nav>{menu}</nav><div class='race_tab'>"
            f"Race 8 - THE CELEBRATION CUP (HANDICAP) Sunday, September 27, 2026, "
            f"Sha Tin, 16:35 Turf, \"C+3\" Course, 1400M Prize Money: $4,200,000, "
            f"{header} SETUP MY STARTER LIST</div>"
            f"<table class='starter'><tr><td>Horse No.</td></tr></table></body></html>")


def test_the_card_reads_its_own_header_not_the_menu() -> None:
    html = _card("-, Group Three", menu="Key Races Group One races Class 1 menu")
    head = rc.parse_race_header(html, 8)
    assert to_race_class(head["race_class"]) == ("G3", 0)
    assert head["off_time"] == "16:35"
    assert head["distance"] == 1400


def test_the_card_keeps_the_restricted_bracket() -> None:
    head = rc.parse_race_header(_card("Rating: 60-40, Class 4 (Restricted)"), 8)
    assert to_race_class(head["race_class"]) == ("4", 1)


@pytest.mark.parametrize("line, expect", [
    ("RACE 9 (341) Class 3 (Restricted) - 1600M - (85-60) Going : GOOD", ("3", 1)),
    ("RACE 7 (394) 4 Year Olds - 1600M Going : GOOD", ("4YO", 1)),
    ("RACE 9 (500) Group Three - 1800M Going : GOOD", ("G3", 0)),
    ("RACE 1 (600) Griffin Race - 1200M Going : GOOD TO YIELDING", ("Griffin Race", 1)),
    ("RACE 4 (100) Class 4 - 1200M - (60-40) Going : GOOD", ("4", 0)),
])
def test_the_results_header_in_all_five_phrasings(line, expect) -> None:
    info = results.parse_race_header(f"<div>{line} Course : TURF - \"A\" Course</div>")
    assert to_race_class(info["race_class"]) == expect
    assert info["distance"] in (1200, 1600, 1800)


def test_one_page_carries_every_race_of_a_meeting() -> None:
    html = ("<div>10/09/2025 07/09/2025 Race 1 Class 5 - 1400M - (40-0) - TURF - "
            "\"C+3\" Course - HUNG SHUI KIU HANDICAP Multiple Pla. Horse No. "
            "Race 9 Class 3 (Restricted) - 1600M - (85-60) - TURF - \"C+3\" Course "
            "- TIN SHUI WAI HANDICAP Pla. Horse</div>")
    heads = parse_meeting_headers(html)
    assert [(h["race_no"], h["race_class"], h["distance"]) for h in heads] == [
        (1, "Class 5", 1400), (9, "Class 3 (Restricted)", 1600)]
    assert heads[1]["race_name"] == "TIN SHUI WAI HANDICAP"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "c.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2024-01-13", "race_no": 9, "venue": "ST", "distance": 1600},
            {"race_date": "2024-01-13", "race_no": 1, "venue": "ST", "distance": 1400,
             "race_class": "Class 5"},
        ])
        upsert.upsert_runners(conn, [
            {"race_date": "2024-01-13", "race_no": n, "horse_no": 1,
             "horse_name": f"H{n}", "place": "1"} for n in (1, 9)])
    conn.close()
    return path


def test_the_store_writes_the_vocabulary_and_the_flag(db) -> None:
    conn = get_conn(db)
    row = conn.execute("SELECT race_class, restricted FROM races WHERE race_no = 1").fetchone()
    conn.close()
    assert (row["race_class"], row["restricted"]) == ("5", 0)


def test_the_repair_finds_the_race_with_no_class_and_fixes_it(db, monkeypatch) -> None:
    d = repair.survey(db)
    assert d.class_dates == ["2024-01-13"] and d.counts["class_wrong"] == 1
    from hkrd.ingest import headers
    monkeypatch.setattr(headers, "fetch_meeting_headers", lambda date, **k: [
        {"race_no": 9, "race_class": "Class 3 (Restricted)", "distance": 1600,
         "race_name": "TIN SHUI WAI HANDICAP"},
        {"race_no": 1, "race_class": "Class 5", "distance": 1400, "race_name": None},
        # A race the archive does not hold is not invented from a header line.
        {"race_no": 5, "race_class": "Class 4", "distance": 1200, "race_name": None}])
    log = repair._repair_classes(db, d.class_dates)
    conn = get_conn(db)
    rows = {r["race_no"]: (r["race_class"], r["restricted"]) for r in conn.execute(
        "SELECT race_no, race_class, restricted FROM races")}
    conn.close()
    assert rows == {1: ("5", 0), 9: ("3", 1)}
    assert any("R9 None→3" in line for line in log)
    assert repair.survey(db).class_dates == []


def test_the_last_race_of_a_meeting_is_read() -> None:
    """The page after a meeting's last race carries a newline. The first
    version required each race's text to run on to the next "Race N" or the
    end of the page, could not cross it, and so never read the last race of
    518 meetings."""
    html = ("<div>Race 7 Class 3 - 1650M - (80-60) - TURF - \"A\" Course - HOI MEI "
            "HANDICAP Pla. 6TH DOUBLE 11/4 163.00 11/5 216.50 Race 8 Class 3 - "
            "1200M - (80-60) - TURF - \"A\" Course - LIDO HANDICAP Multi Angle</div>"
            "<p>Remarks\nfor the meeting</p>")
    heads = parse_meeting_headers(html)
    assert [(h["race_no"], h["race_name"]) for h in heads] == [
        (7, "HOI MEI HANDICAP"), (8, "LIDO HANDICAP")]
