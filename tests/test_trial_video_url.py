"""A barrier trial's video is not a race replay.

Every trial link in the dashboard was built by calling `replayUrl` with a trial
date and a batch number. That produces a well-formed URL — for a RACE that does
not exist. Nothing threw, nothing 404'd in a way anyone saw; the player simply
had nothing to play, on every trial link on every page.

This is the class of bug a string-building helper invites: two URLs that look
alike, one function, and no way to tell from the call site which one you asked
for. So the two real URLs the owner supplied are pinned here, exactly, and the
test drives the actual vocab.js rather than a Python restatement of it — a
second copy of the rule would be the thing that drifts.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

VOCAB = Path(__file__).resolve().parent.parent / "web" / "assets" / "vocab.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The two the owner sent, verbatim. Aug 31 is the newest trial day (live page),
# 7 Jul is behind the archive; ch is Conghua and st is Sha Tin.
AUG31 = ("https://racing.hkjc.com/contentAsset/videoplayer_v4/"
         "video-player-iframe_v4.html?type=brts&date=20260831&rc=ch&no=01"
         "&lang=eng&rf=http://racing.hkjc.com/en-us/local/information/btresult"
         "&pageid=racing/local")
JUL07 = ("https://racing.hkjc.com/contentAsset/videoplayer_v4/"
         "video-player-iframe_v4.html?type=brts&date=20260707&rc=st&no=01"
         "&lang=eng&rf=http://racing.hkjc.com/en-us/local/information/archive/"
         "btresult?Date=2026/07/07&pageid=racing/local")


def _call(expr: str):
    """Evaluate an expression against the real vocab.js and return its value."""
    script = (f"import {{ trialReplayUrl, replayUrl }} from {str(VOCAB)!r};\n"
              f"process.stdout.write(JSON.stringify({expr}));\n")
    out = subprocess.run(  # noqa: S603 - a JS unit under test needs a JS runtime
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _parts(url: str) -> tuple[str, dict]:
    """Path and query, so the assertion is about the URL and not its key order."""
    bits = urlsplit(url)
    # `rf` carries its own `?Date=` for an archived day, so the query cannot be
    # split naively — that second `?` is part of the rf value.
    head, _, rest = bits.query.partition("&rf=")
    rf, _, tail = rest.partition("&pageid=")
    q = parse_qs(head, keep_blank_values=True)
    q["rf"] = [rf]
    q["pageid"] = [tail]
    return f"{bits.scheme}://{bits.netloc}{bits.path}", q


def test_the_newest_trial_day_matches_the_live_page_url() -> None:
    got = _call("trialReplayUrl('2026-08-31', 1, 'CH', { archived: false })")
    assert _parts(got) == _parts(AUG31)


def test_an_older_trial_day_matches_the_archive_url() -> None:
    got = _call("trialReplayUrl('2026-07-07', 1, 'ST', { archived: true })")
    assert _parts(got) == _parts(JUL07)


def test_a_trial_is_never_addressed_as_a_race() -> None:
    """The whole bug in one assertion."""
    trial = _call("trialReplayUrl('2026-08-31', 1, 'HV')")
    race = _call("replayUrl('2026-08-31', 1)")
    assert "type=brts" in trial and "type=replay-full" not in trial
    assert "type=replay-full" in race and "type=brts" not in race
    assert trial != race


def test_every_racecourse_that_holds_trials_is_addressable() -> None:
    """Three, not two. Conghua is on the mainland and is the one a two-track
    assumption drops — the Aug 31 batch the owner sent was run there."""
    for venue, rc in (("ST", "st"), ("HV", "hv"), ("CH", "ch")):
        assert f"rc={rc}" in _call(f"trialReplayUrl('2026-08-31', 1, {venue!r})")


def test_an_unknown_course_yields_no_link_rather_than_a_wrong_one() -> None:
    """A link to another track's trial plays. It is a different set of horses,
    and nothing on screen would say so — worse than no link at all."""
    assert _call("trialReplayUrl('2026-08-31', 1, null)") is None
    assert _call("trialReplayUrl('2026-08-31', 1, 'XX')") is None
    assert _call("trialReplayUrl('2026-08-31', 1, '')") is None


def test_the_batch_number_is_padded() -> None:
    """`no=1` is not the same address as `no=01`."""
    assert "no=01" in _call("trialReplayUrl('2026-08-31', 1, 'ST')")
    assert "no=11" in _call("trialReplayUrl('2026-08-31', 11, 'ST')")


def test_a_jump_offset_is_included_only_when_known() -> None:
    """It seeks to where the batch jumps. Absent, the clip starts a little
    early — which is a working link, not a broken one."""
    assert "jumpTime" not in _call("trialReplayUrl('2026-08-31', 1, 'ST')")
    assert "jumpTime=85" in _call(
        "trialReplayUrl('2026-08-31', 1, 'ST', { jumpTime: 85 })")
