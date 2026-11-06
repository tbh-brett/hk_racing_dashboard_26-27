"""The fund API: hkrd's read-only window for the HKTB fund app (docs/fund-api.md).

Every answer is checked against docs/fund-api.schema.json, the contract the
fund app is built to, by a small validator below (jsonschema is not a
dependency here). The rules that matter most to the fund app each have a test:
999.0 is never a price, a withdrawal is never a run, unchanged content answers
304, and nothing answers without the token.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hkrd.query import fund_results as fr
from hkrd.store import coerce, upsert
from hkrd.store.connect import get_conn, init_db, transaction

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs" / "fund-api.schema.json").read_text(encoding="utf-8"))
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
D0, D, D2 = "2026-10-01", "2026-11-11", "2026-11-15"
# Place odds on the captures of race 1: the last one is the tote's closing price.
PLACE = {(1, "2026-11-11T18:20:00", 1): "1.3", (1, "2026-11-11T18:40:00", 1): "1.2",
         (1, "2026-11-11T18:40:00", 2): "1.5", (1, "2026-11-11T18:40:00", 3): "3.4"}


# ── the contract ────────────────────────────────────────────────────────────

def _is(value: object, kind: str) -> bool:
    return {"object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str), "boolean": isinstance(value, bool),
            "null": value is None,
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            }[kind]


def violations(inst: object, schema: dict, path: str = "$") -> list[str]:
    """Every way `inst` breaks `schema`: the keywords the contract uses."""
    if "$ref" in schema:
        return violations(inst, SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[1]], path)
    errs: list[str] = []
    if "oneOf" in schema:
        fits = [s for s in schema["oneOf"] if not violations(inst, s, path)]
        if len(fits) != 1:
            return [f"{path}: matches {len(fits)} of oneOf"]
    for rule in schema.get("allOf", []):
        if "if" in rule and not violations(inst, rule["if"], path):
            errs += violations(inst, rule["then"], path)
    if kind := schema.get("type"):
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is(inst, k) for k in kinds):
            return errs + [f"{path}: {inst!r} is not {kind}"]
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: {inst!r} is not {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if "minimum" in schema and _is(inst, "number") and inst < schema["minimum"]:
        errs.append(f"{path}: {inst} below {schema['minimum']}")
    if isinstance(inst, dict):
        errs += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in inst]
        props, extra = schema.get("properties", {}), schema.get("additionalProperties")
        for k, v in inst.items():
            if k in props:
                errs += violations(v, props[k], f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unexpected {k}")
            elif isinstance(extra, dict):
                errs += violations(v, extra, f"{path}.{k}")
    if isinstance(inst, list):
        if len(inst) < schema.get("minItems", 0) or len(inst) > schema.get("maxItems", 1e9):
            errs.append(f"{path}: {len(inst)} items")
        for i, v in enumerate(inst):
            errs += violations(v, schema.get("items", {}), f"{path}[{i}]")
    return errs


def conforms(body: object) -> object:
    errs = violations(body, SCHEMA)
    assert not errs, "\n".join(errs)
    return body


# ── a meeting ───────────────────────────────────────────────────────────────

def _runner(date, race_no, horse_no, name, place=None, lbw=None, odds=None):
    return {"race_date": date, "race_no": race_no, "horse_no": horse_no,
            "horse_name": name, "place": place, "lengths_behind": lbw,
            "draw": horse_no, "jockey": "J Rider", "trainer": "T Trainer",
            "win_odds": odds}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "fund.db"
    monkeypatch.setenv("HKRD_DB", str(path))
    monkeypatch.setenv("FUND_API_TOKEN", TOKEN)
    conn = get_conn(path)
    init_db(conn)
    races = [{"race_date": D0, "race_no": 1, "venue": "ST", "course": "A",
              "surface": "Turf", "distance": 1400, "race_class": "3", "off_time": "13:00"},
             {"race_date": D2, "race_no": 1, "venue": "ST", "course": "AWT",
              "surface": "AWT", "distance": 1650, "race_class": "4", "off_time": "19:15"}]
    runners = [_runner(D0, 1, 1, "ALPHA", "3", "1-1/2", "6.0"),
               _runner(D0, 1, 2, "ECHO", "1", "-", "2.5"),
               _runner(D0, 1, 3, "FOXTROT", "2", "SH", "4.0"),
               _runner(D0, 1, 4, "GOLF", "WV"),
               _runner(D2, 1, 1, "HOTEL")]
    for n in range(1, 12):           # eleven races: two Six Ups
        h, m = divmod(18 * 60 + 45 + 30 * (n - 1), 60)
        races.append({"race_date": D, "race_no": n, "venue": "HV", "course": "B",
                      "surface": "Turf", "distance": 1200, "race_class": "4",
                      "off_time": f"{h:02d}:{m:02d}"})
        if n > 1:
            runners += [_runner(D, n, h, f"R{n} H{h}", str(h) if n == 2 else None,
                                "-" if h == 1 else f"{h}", "5.0" if n == 2 else None)
                        for h in range(1, 5)]
    runners += [_runner(D, 1, 1, "ALPHA", "1", "-", "4.4"),
                _runner(D, 1, 2, "BRAVO", "2", "N", "2.0"),
                _runner(D, 1, 3, "CHARLIE", "3", "3-1/4", "15"),
                _runner(D, 1, 4, "DELTA", "WV")]
    snaps = [(1, "2026-11-11T18:20:00", {1: "2.0", 2: "4.0", 3: "999", 4: None}),
             (1, "2026-11-11T18:40:00", {1: "1.9", 2: "4.6", 3: "14"}),
             (2, "2026-11-11T18:10:00", {1: "3.0", 2: "3.5", 3: "6.0", 4: "9.0"}),
             (2, "2026-11-11T18:30:00", {1: "999", 2: "999", 3: "999", 4: "999"})]
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": D, "race_no": r, "horse_no": h, "captured_at": at, "win_odds": w,
             "place_odds": PLACE.get((r, at, h))}
            for r, at, prices in snaps for h, w in prices.items()])
        upsert.upsert_market_close(conn, [
            {"race_date": D, "race_no": 1, "closed_at": "2026-11-11T18:45:00", "status": "STOP_SELL"},
            {"race_date": D, "race_no": 2, "closed_at": "2026-11-11T19:15:00", "status": "STOP_SELL"}])
        upsert.upsert_pool_turnover(conn, [
            {"race_date": D, "race_no": 1, "pool": "SixUP", "captured_at": "2026-11-11T18:00:00", "turnover": 100},
            {"race_date": D, "race_no": 1, "pool": "SixUP", "captured_at": "2026-11-11T18:30:00", "turnover": 250},
            {"race_date": D, "race_no": 6, "pool": "SixUP", "captured_at": "2026-11-11T18:30:00", "turnover": None},
            {"race_date": D, "race_no": 1, "pool": "WIN", "captured_at": "2026-11-11T18:30:00", "turnover": 9}])
        upsert.upsert_dividends(conn, [
            {"race_date": D, "race_no": 1, "pool": p, "combination": c, "dividend_per_10": v}
            for p, c, v in (("WIN", "1", 44.0), ("PLACE", "1", 15.5), ("PLACE", "2", 11.0),
                            ("PLACE", "3", 20.0), ("QIN", "1,2", 50.0))])
        upsert.upsert_comments(conn, [
            {"race_date": D, "race_no": 1, "horse_no": 1, "source": "incident",
             "comment_text": "Slow to begin. Raced wide and without cover for the majority of the event."},
            {"race_date": D, "race_no": 1, "horse_no": 3, "source": "incident",
             "comment_text": "A veterinary inspection immediately following the race did not show any significant findings."}])
        conn.execute("INSERT INTO horse_name_zh VALUES ('ALPHA', '甲', 'H001', 'racecard_zh', ?)", (D,))
        conn.execute("INSERT INTO horse_background (horse_name, brand_no, profiled_for, path, url, "
                     "import_type, fetched_at) VALUES ('BRAVO', 'H002', ?, 'p', 'u', 'PP', ?)", (D, D))
        conn.execute("INSERT INTO horse_name_zh VALUES ('NEVER RAN', '乙', 'H009', 'racecard_zh', ?)", (D,))
        conn.execute("INSERT INTO job_runs VALUES ('scrape_meeting:card', '2026-11-11T00:59:00+00:00', "
                     "'2026-11-11T01:00:00+00:00', 1, 'ok')")
        conn.execute("INSERT INTO job_runs VALUES ('scrape_meeting:results', '2026-11-11T11:00:00+00:00', "
                     "'2026-11-11T11:01:00+00:00', 1, 'ok')")
    conn.close()
    return path


@pytest.fixture()
def client(db):
    from hkrd.api.fund_app import app
    return TestClient(app)


def get(client, url, **headers):
    return client.get(url, headers={**AUTH, **headers})


# ── tests ───────────────────────────────────────────────────────────────────

def test_nothing_answers_without_the_token(client, monkeypatch):
    assert client.get("/fund/v1/health").status_code == 401
    assert client.get("/fund/v1/health", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert get(client, "/fund/v1/health").json()["ok"] is True
    monkeypatch.delenv("FUND_API_TOKEN")
    r = get(client, "/fund/v1/health")
    assert r.status_code == 503 and "FUND_API_TOKEN" in r.json()["detail"]


def test_card_has_brand_numbers_scratchings_and_post_times(client):
    body = conforms(get(client, f"/fund/v1/meetings/{D}/HV/card").json())
    assert body["kind"] == "card" and body["meeting"] == {"date": D, "venue": "HV", "course": "B"}
    assert body["updated_at"] == "2026-11-11T09:00:00+08:00"
    first = body["races"][0]
    assert (first["post_time"], first["class"], first["track"]) == (
        "2026-11-11T18:45:00+08:00", "Class 4", "turf")
    by_name = {r["name"]: r for r in first["runners"]}
    assert by_name["ALPHA"]["brand_no"] == "H001" and by_name["ALPHA"]["name_zh"] == "甲"
    assert by_name["BRAVO"]["brand_no"] == "H002"         # from the new-horse profile
    assert by_name["CHARLIE"]["brand_no"] is None          # never paired: null, not dropped
    assert by_name["DELTA"]["scratched"] and not by_name["ALPHA"]["scratched"]


def test_the_wrong_meeting_is_404_and_a_bad_date_400(client):
    assert get(client, f"/fund/v1/meetings/{D}/ST/card").status_code == 404
    assert "no meeting" in get(client, "/fund/v1/meetings/2026-11-12/HV/card").json()["detail"]
    assert get(client, "/fund/v1/meetings/11-11-2026/HV/card").status_code == 400
    assert get(client, f"/fund/v1/meetings/{D}/XX/card").status_code == 400


def test_unchanged_content_answers_304_whenever_hkrd_rereads_it(client, db):
    first = get(client, f"/fund/v1/meetings/{D}/HV/card")
    etag = first.headers["etag"]
    assert etag == f'W/"{first.json()["version"]}"'
    again = get(client, f"/fund/v1/meetings/{D}/HV/card", **{"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == etag
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("INSERT INTO job_runs VALUES ('scrape_meeting:card', '2026-11-11T05:00:00+00:00', "
                     "'2026-11-11T05:01:00+00:00', 1, 'ok')")
    conn.close()
    later = get(client, f"/fund/v1/meetings/{D}/HV/card").json()
    assert later["updated_at"] == "2026-11-11T13:01:00+08:00"
    assert later["version"] == first.json()["version"]


def test_odds_take_each_races_latest_priced_capture_and_never_999(client):
    url = f"/fund/v1/meetings/{D}/HV/odds"
    body = conforms(get(client, f"{url}?at=2026-11-11T18:35:00%2B08:00").json())
    races = {r["race_no"]: r for r in body["odds"]["races"]}
    assert races[1] == {"race_no": 1, "taken_at": "2026-11-11T18:20:00+08:00",
                        "win": {"1": 2.0, "2": 4.0}}       # 999 and the blank dropped
    # Race 2's newer capture is 999 throughout: the price is the one before it.
    assert races[2]["taken_at"] == "2026-11-11T18:10:00+08:00"
    assert body["odds"]["label"] == "snapshot"
    assert body["odds"]["taken_at"] == "2026-11-11T18:20:00+08:00"
    late = get(client, f"{url}?at=2026-11-11T20:00:00%2B08:00").json()
    assert late["odds"]["label"] == "close"
    assert late["odds"]["races"][0]["win"] == {"1": 1.9, "2": 4.6, "3": 14.0}
    early = get(client, f"{url}?at=2026-11-11T17:00:00%2B08:00")
    assert early.status_code == 404 and "no win odds" in early.json()["detail"]
    assert get(client, f"{url}?at=tonight").status_code == 400


def test_an_eleven_race_card_has_two_six_ups(client):
    body = conforms(get(client, f"/fund/v1/meetings/{D}/HV/pools").json())
    assert body["pools"] == [
        {"type": "six_up_early", "legs": [1, 2, 3, 4, 5, 6], "investment_hkd": 250.0, "jackpot_hkd": None},
        {"type": "six_up", "legs": [6, 7, 8, 9, 10, 11], "investment_hkd": None, "jackpot_hkd": None}]
    assert body["updated_at"] == "2026-11-11T18:30:00+08:00"
    assert get(client, f"/fund/v1/meetings/{D0}/ST/pools").status_code == 404


def test_results_settle_from_this_alone(client):
    body = conforms(get(client, f"/fund/v1/meetings/{D}/HV/results").json())
    assert [r["race_no"] for r in body["results"]] == [1, 2]
    assert "pool_dividends" not in body
    race = body["results"][0]
    assert race["finish"] == [
        {"horse_no": 1, "pos": 1, "margin": "", "win_odds_close": 4.4,
         "place_odds_close": 1.2, "incidents": ["slow away", "wide"]},
        {"horse_no": 2, "pos": 2, "margin": "N", "win_odds_close": 2.0,
         "place_odds_close": 1.5},
        {"horse_no": 3, "pos": 3, "margin": "3-1/4", "win_odds_close": 15.0,
         "place_odds_close": 3.4},
        {"horse_no": 4, "pos": None, "scratched": True}]
    # Race 2 was captured without place odds: no closing place price, not a zero.
    assert all("place_odds_close" not in f for f in body["results"][1]["finish"])
    assert race["win_dividends"] == [{"horse_no": 1, "per_10_hkd": 44.0}]
    assert [d["horse_no"] for d in race["place_dividends"]] == [3, 1, 2]
    pending = get(client, f"/fund/v1/meetings/{D2}/ST/results")
    assert pending.status_code == 404 and "no results yet" in pending.json()["detail"]


def test_a_horses_runs_by_brand_number(client):
    body = conforms(get(client, "/fund/v1/horses/H001/runs?since=2026-09-01").json())
    assert [(r["date"], r["pos"], r["field_size"], r["margin"]) for r in body["runs"]] == [
        (D0, 3, 3, "1-1/2"), (D, 1, 3, "")]               # the withdrawal is no starter
    assert body["runs"][0]["class"] == "Class 3" and body["runs"][1]["incidents"] == ["slow away", "wide"]
    assert "place_odds_close" not in body["runs"][0] and body["runs"][1]["place_odds_close"] == 1.2
    assert len(get(client, f"/fund/v1/horses/H001/runs?since={D}").json()["runs"]) == 1
    quiet = conforms(get(client, "/fund/v1/horses/H009/runs?since=2026-09-01").json())
    assert quiet["runs"] == []                            # known horse, no runs: a true answer
    assert get(client, "/fund/v1/horses/Z999/runs?since=2026-09-01").status_code == 404


def test_meetings_between_two_dates(client):
    body = conforms(get(client, "/fund/v1/meetings?from=2026-09-01&to=2026-11-30").json())
    assert [(m["date"], m["venue"], m["races"], m.get("course")) for m in body["meetings"]] == [
        (D0, "ST", 1, "A"), (D, "HV", 11, "B"), (D2, "ST", 1, "AWT")]
    hv = body["meetings"][1]
    assert (hv["first_post"], hv["last_post"]) == ("2026-11-11T18:45:00+08:00", "2026-11-11T23:45:00+08:00")
    assert get(client, "/fund/v1/meetings?from=2026-11-30&to=2026-09-01").status_code == 400
    assert get(client, "/fund/v1/meetings?from=2027-08-01&to=2027-08-10").json()["meetings"] == []


def test_health_conforms(client):
    conforms(get(client, "/fund/v1/health").json())


def test_the_examples_handed_to_the_fund_side_conform():
    for example in json.loads((ROOT / "docs" / "fund-api.example.json").read_text(encoding="utf-8")):
        conforms(example)


def test_fund_routes_are_never_on_the_public_app():
    from hkrd.api.app import app as dashboard
    from hkrd.api.fund_app import app as fund
    from hkrd.api.routes import fund as fund_routes
    assert not [p for p in dashboard.openapi()["paths"] if p.startswith("/fund")]
    # The OpenAPI schema names every path an app serves (see test_smoke).
    served = set(fund.openapi()["paths"])
    assert {r.path for r in fund_routes.router.routes} <= served
    assert all(p.startswith("/fund/v1/") for p in served)


@pytest.mark.parametrize("text, tags", [
    ("Jumped awkwardly, shifted out and brushed GOOD FORTUNE.", ["slow away", "bumped"]),
    ("Shifted out abruptly on jumping and made heavy contact with SHOW ME YOUR LOVE.", ["bumped"]),
    ("For the major part of the Home Straight failed to obtain clear running.", ["held up"]),
    ("A veterinary inspection immediately following the race did not show any significant findings.", []),
    ("had previously been diagnosed with an irregular heart rhythm.", []),
    ("A veterinary inspection immediately following the race revealed that the horse was "
     "displaying an irregular heart rhythm.", ["vet finding"]),
    ("Noted to have lost its left hind plate after the race.", ["lost a shoe"]),
    ("No report.", []), (None, []),
])
def test_incident_tags(text, tags):
    assert fr.incident_tags(text) == tags


@pytest.mark.parametrize("margin", ["3-1/4", "1/2", "3/4", "N", "HD", "SH", "NOSE", "4", "10-1/2"])
def test_margins_go_back_as_hkjc_printed_them(margin):
    assert coerce.format_lbw(coerce.parse_lbw(margin)) == margin


# ── the launcher ────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(dash, fund, sock) -> threading.Thread:
    from hkrd import listen
    t = threading.Thread(target=lambda: asyncio.run(listen.serve(dash, fund, sock)), daemon=True)
    t.start()
    deadline = time.time() + 20
    while not (dash.started and (fund is None or fund.started)):
        assert time.time() < deadline, "servers did not start"
        time.sleep(0.05)
    return t


def _read(url: str, **headers) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as r:
        return json.loads(r.read())


def test_one_process_serves_the_dashboard_and_the_fund_api(db):
    from hkrd import listen
    port, fund_port = _free_port(), _free_port()
    dash, fund, sock = listen.build("127.0.0.1", port, fund_port)
    assert fund is not None
    t = _run(dash, fund, sock)
    try:
        assert _read(f"http://127.0.0.1:{port}/api/health") == {"ok": True}
        assert _read(f"http://[::1]:{fund_port}/fund/v1/health", **AUTH)["ok"] is True
        assert _read(f"http://127.0.0.1:{fund_port}/fund/v1/health", **AUTH)["ok"] is True
    finally:
        dash.should_exit = True
        t.join(15)
    assert not t.is_alive() and fund.should_exit     # the dashboard stopping stops both


def test_a_fund_port_that_cannot_bind_leaves_the_dashboard_up(db):
    from hkrd import listen
    port = _free_port()
    with socket.socket(socket.AF_INET6) as taken:
        taken.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        taken.bind(("::", 0))
        taken.listen()
        dash, fund, sock = listen.build("127.0.0.1", port, taken.getsockname()[1])
        assert fund is None and sock is None
        t = _run(dash, fund, sock)
        try:
            assert _read(f"http://127.0.0.1:{port}/api/health") == {"ok": True}
        finally:
            dash.should_exit = True
            t.join(15)


def test_no_token_no_fund_listener(db, monkeypatch):
    from hkrd import listen
    monkeypatch.delenv("FUND_API_TOKEN")
    assert listen.build("127.0.0.1", _free_port(), _free_port())[1] is None
