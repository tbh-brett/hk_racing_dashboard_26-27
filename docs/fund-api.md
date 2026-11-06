# Fund API: hkrd's side

A read-only window for the HKTB fund app, from the handoff's `fund_api.md`. Plain
database reads only: no route starts a scrape, a model or the simulator.

**Status, 6 Nov 2026:** built and tested locally, not yet deployed. The contract as
hkrd serves it is `docs/fund-api.schema.json`, with examples in
`docs/fund-api.example.json`. Both are amended copies of the handoff's: the
changes are listed under "Contract changes" below, and the fund side must take
these copies.

## Endpoints

All need `Authorization: Bearer $FUND_API_TOKEN`. Each answer carries a `version`,
sent as a weak ETag; sending it back in `If-None-Match` gets a 304 with no body.

| Route | Answer |
| --- | --- |
| `GET /fund/v1/meetings?from=&to=` | every meeting hkrd has a card for; an empty range is `[]` |
| `GET /fund/v1/meetings/{date}/{venue}/card` | runners with brand number, name_zh, draw, jockey, trainer, scratched |
| `GET /fund/v1/meetings/{date}/{venue}/odds?at=ISO` | per race, the latest capture at or before `at` with a price; 404 if none |
| `GET /fund/v1/meetings/{date}/{venue}/pools` | Six Up legs and investment; 404 if no pool was captured |
| `GET /fund/v1/meetings/{date}/{venue}/results` | the races run so far: order, margins, closing win and place odds, dividends; 404 before the first |
| `GET /fund/v1/horses/{brand_no}/runs?since=` | the horse's runs on or after `since`; 404 for an unknown brand |
| `GET /fund/v1/health` | `{ok, db_updated_at}` |

Measured on the 29 Oct production copy, through the app: 9 to 28 ms an answer
(results is the slowest), and a 304 costs the same as a 200 because the version
is a hash of the built answer. The card is 22 KB, or 5 KB gzipped.

## Decisions (checked against the 29 Oct production copy)

- **Brand number.** `runners` has none. It's read as
  `COALESCE(horse_name_zh.brand_no, horse_background.brand_no)`, joined on
  `horse_name`. This covers 1,916 of this season's 1,949 runners; every miss is
  6 to 16 Sep, before the name sync ran (it runs at 07:30 and 13:30 daily). The
  field is nullable. `/horses/{brand_no}/runs` finds the horse's name and reads
  its runs by name.
- **Six Up.** `odds_pool_turnover` pool `SixUP` is filed under its first leg,
  and the legs are six consecutive races. An 11-race card has two (25 Oct: first
  legs 1 and 6): the earlier is `six_up_early`. Investment is the latest
  turnover row, null while the pool is closed. **hkrd captures neither the
  jackpot nor Six Up dividends**: `jackpot_hkd` is null and `pool_dividends` is
  not sent, so the fund's admin enters both. More than two pools, or legs past
  the last race, raise an error (a 500): that's a data fault.
- **Odds.** For each race, the newest capture at or before `at` that holds a
  price below 999.0. A capture that's 999.0 throughout is skipped for the one
  before it. 999.0 and blanks are dropped from the answer. Each race carries its
  own `taken_at`. The label is `close` once every race returned had stopped
  selling by `at`, and `snapshot` otherwise: hkrd doesn't know the fund's lock.
- **Closing tote odds, for the Classic.** The Classic is struck at FIXED prices
  on Australian books (entered by hand when each bet is placed), and the fund
  measures each leg against the HKJC tote. A leg that paid is measured against
  its dividend; a leg that didn't is measured against the tote's closing price.
  So results and runs carry `win_odds_close` (`runners.win_odds`) and
  `place_odds_close`, the last capture's place odds: `runners` has no place
  column, and the last capture is the settled price now that capturing stops
  at the close. On 25 Oct R1 those closing place odds equal the place dividends
  paid (1.5, 3.9 and 1.7 against $15.00, $39.00 and $17.00).
- **Margins.** `lengths_behind` goes back to HKJC's text through
  `store/coerce.format_lbw`, the inverse of `parse_lbw` on the same table
  (3.25 is "3-1/4", 0.3 is "N"). The winner's margin is "".
- **Scratched** means `place_code` is WV, WV-A, WX, WX-A or WXNR. Such a runner is
  in results with `pos: null, scratched: true`, and is never a run or a starter.
- **Field size** (runs) is the race's starters, grouped once in a CTE.
- **Incident tags** come from a fixed regex vocabulary in
  `query/fund_results.INCIDENT_TAGS`, applied to the stewards' report. This
  season it tags 1,028 of 1,831 reports; most of the rest say "No report." or
  "Sent for sampling". "did not show any significant findings", an earlier
  diagnosis and blood in the mouth are never a vet finding. The tags are
  descriptive only: stewards' words don't beat the price (5 Oct 2026).
- **Post time** is `races.off_time` plus `+08:00`. It's null for the 2025/26
  archive, which never carried one.
- **`version`** is the first 16 hex characters of a sha256 of the answer, with
  `version` and `updated_at` left out and keys sorted. **`updated_at`** is when
  hkrd last read this from HKJC (the last good `scrape_meeting:card` or
  `:results` run, or the newest capture for odds and pools). It moves on every
  re-scrape, which is why it's kept out of the version. It's null if hkrd has no
  record.

## Serving

- `hkrd/api/fund_app.py` is its own FastAPI app with GZip and a bearer check.
  It fails closed: 503 if `FUND_API_TOKEN` isn't set, 401 if the token is wrong.
  It has no docs URLs.
- `hkrd/api/routes/fund.py` is kept out of `routes.__all__`, which is what the
  public app mounts. A test checks that no `/fund` path is on the dashboard.
- The reads live in `hkrd/query/fund.py` and `hkrd/query/fund_results.py`.
- `hkrd/listen.py` runs two uvicorn servers on one event loop. The dashboard is
  served as before. The fund API gets a socket hkrd binds itself on
  `[::]:8081`, and only when the token is set. If that bind fails it's logged
  and the dashboard carries on; when the dashboard stops, so does the fund API.
  `ops/entrypoint.sh` runs `python -m hkrd.listen` where it ran `uvicorn`.
- hkrd never auto-stops (`min_machines_running = 1`), so the fund app needs no
  Flycast. The region is `sin`: deploy the fund app there.
- To switch it on: `docs/deploy.md`, "The fund API".

## Contract changes (hand these files to the fund side)

1. `brand_no`, `post_time` and `updated_at` can be null.
2. Each race in `odds.races` has its own required `taken_at`.
3. The odds `label` is only ever `snapshot` or `close`.
4. Results finish rows may carry `scratched: true`.
5. No `pool_dividends`; `jackpot_hkd` is null.
6. `health` is a schema of its own, in the top-level `oneOf`.
7. ETags are weak (`W/"…"`); the fund client already sends back whatever it got.
8. Results finish rows and runs carry `place_odds_close`.

## Found in passing

`ingest/dividends` never stores doubles. HKJC labels them "8TH DOUBLE", and
`_pool_for` matches labels by prefix, so there are no DBL rows since Sep 2025.
