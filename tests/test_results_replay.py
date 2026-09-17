"""The Results page plays the race it is showing — through the one URL builder.

The replay on Results is HKJC's own player, framed. Measured before building it
(2026-09-17): the player page sends no X-Frame-Options and no frame-ancestors,
and framed on this dashboard's origin it streams the same MP4 from HKJC's CDN
that it streams on racing.hkjc.com. What can still go wrong is the address, and
this dashboard has already shipped that bug once: every trial link was built by
a helper meant for races and played nothing (see test_trial_video_url.py).

So two things are pinned. The race URL matches what HKJC's own results page
links for the same race, on the parts that address the video. And the page
builds no video URL of its own — a second copy of the pattern is the thing that
drifts, silently, into a player with nothing to play.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "web" / "assets"
VOCAB = ASSETS / "vocab.js"
RESULTS = ASSETS / "results.js"
NODE = shutil.which("node")

# HKJC's results page for 16 Sep 2026, race 1 — its "Multi Angle Race Replay"
# link, verbatim. `videoParam` is PD there and P in `replayUrl`; both were
# loaded and both offer the race, patrol, leading-horse and drone angles, so the
# difference is not part of what addresses the video and is not pinned.
HKJC_RACE_1 = ("https://racing.hkjc.com/contentAsset/videoplayer_v4/"
               "video-player-iframe_v4.html?type=replay-full&date=20260916&no=01"
               "&lang=eng&noPTbar=false&noLeading=false&videoParam=PD"
               "&rf=http://racing.hkjc.com/en-us/local/information/localresults"
               "?racedate=2026/09/16&pageid=racing/local")

ADDRESS = ("type", "date", "no", "lang")


def _address(url: str) -> tuple[str, dict]:
    bits = urlsplit(url)
    query = parse_qs(bits.query.partition("&rf=")[0])
    return (f"{bits.scheme}://{bits.netloc}{bits.path}",
            {k: query.get(k) for k in ADDRESS})


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_replay_is_the_video_hkjc_links_for_that_race() -> None:
    script = (f"import {{ replayUrl }} from {VOCAB.as_uri()!r};\n"
              "process.stdout.write(JSON.stringify(replayUrl('2026-09-16', 1)));\n")
    out = subprocess.run(  # noqa: S603 - a JS unit under test needs a JS runtime
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert _address(json.loads(out.stdout)) == _address(HKJC_RACE_1)


def test_the_results_page_builds_no_video_url_of_its_own() -> None:
    source = RESULTS.read_text(encoding="utf-8")
    assert re.search(r"\breplayUrl\b", source), \
        "the Results replay must come from vocab.replayUrl"
    for literal in ("videoplayer", "video-player-iframe", "type=replay",
                    "type=passthrough"):
        assert literal not in source, (
            f"results.js spells out {literal!r} itself — that is a second copy "
            "of the HKJC address, and a copy is what drifts")
