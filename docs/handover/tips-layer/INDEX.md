# tips-layer handover — contents

| File | What it is |
|---|---|
| `README.md` | Orientation: what this builds, the PC/Fly split and why, the five sources and what each actually gives you, acceptance criteria, what not to do. Read first. |
| `SPEC.md` | Buildable detail: schema DDL, module map, payload contract, per-source resolution rules, quality tiers, endpoints, panel layout, phases, open questions. |
| `payload.example.json` | A real fixture. Every `factcheck` quote is verbatim from the 23 Sep Happy Valley preview's human-written subtitles. Copy it to `tests/fixtures/`. |
| `PROMPT.md` | The prompt to paste into Claude Code. Says the same as the above in fewer words, plus the non-negotiables. |
| `../../../tools/harvest_youtube.py` | PC-side. Playlists and channels, ANDROID caption route, prefers human subtitles over ASR, splits previews by race chapter. Tested. |
| `../../../tools/harvest_threads.py` | PC-side. Polls Horse Detective through Open RSS. Tested. |
| `../../../tools/harvest_oncc.py` | PC-side. Harvests on.cc's expiring tipster archive to disk. Tested. |

## Drop-in

```
cp -r docs/handover/tips-layer  <repo>/docs/handover/
cp tools/harvest_*.py           <repo>/tools/
```

Then paste `PROMPT.md` into Claude Code from inside the repo.

## Harvest commands worth running before you start

All three on the Windows machine, not Fly.io.

```bash
# 賽馬Fact Check — the best source: human-written zh-HK subtitles
python tools/harvest_youtube.py --channel UCNpEBQatm4NALFlS-HO1nzg \
       --lang zh-HK yue --season-year 2026 --out ./raw/factcheck

# Racing To Win — connections' interviews, English
python tools/harvest_youtube.py --playlist PLK8zYRjJwINk \
       --season-year 2026 --out ./raw/rtw
python tools/harvest_youtube.py --playlist PLRyiXGt4yq8YzlS_FNVmbSr7glDAtUgwt \
       --season-year 2025 --out ./raw/rtw        # last season, for scoring later

# 全方位Bryan — Cantonese, weakest; collect now, resolve last
python tools/harvest_youtube.py --channel UCQAbEL38om9qqgVHGuK5BSg \
       --lang yue zh-HK --season-year 2026 --out ./raw/bryan

# the two that are losing data while they are not running
python tools/harvest_oncc.py --from 2026-03-20 --to 2026-09-22 --out ./raw/oncc
python tools/harvest_threads.py --out ./raw/threads --watch 1800
```
