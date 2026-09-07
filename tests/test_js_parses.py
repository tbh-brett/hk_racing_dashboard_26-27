"""Every shipped script parses.

This exists because a syntax error in a page asset is the quietest failure in
the whole project. Python is exercised by 1,100 tests; the browser code is not,
and a broken string literal in one module takes the whole page down with a
console error nobody sees until they open the page — after a deploy, on a race
day, on a phone.

It has happened twice. Once a `\\n` written into a string became a real newline
and ended the literal; once an escaped apostrophe did not survive being written
to disk. Both shipped, both were found by opening the page.

Node parses the file and says nothing else about it — this is not a linter and
makes no claim about whether the code is right. It only says the browser will
get as far as running it. Skipped where node is not installed, because a
missing tool must not fail a suite that is otherwise about the data.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "web" / "assets"

NODE = shutil.which("node")

# Copied to a `.mjs` name for the check: these are ES modules — they use
# `import` and `export` — and node reads a bare `.js` as CommonJS, where an
# import statement is itself a syntax error.
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def scripts() -> list[Path]:
    return sorted(p for p in ASSETS.glob("*.js"))


def test_there_are_scripts_to_check() -> None:
    """A glob that silently matches nothing would make this file decoration."""
    assert len(scripts()) >= 10


@pytest.mark.parametrize("script", scripts(), ids=lambda p: p.name)
def test_a_shipped_script_parses(script: Path, tmp_path: Path) -> None:
    copy = tmp_path / f"{script.stem}.mjs"
    copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    done = subprocess.run([NODE, "--check", str(copy)],
                          capture_output=True, text=True)
    assert done.returncode == 0, (
        f"{script.name} does not parse — the page it is on will not run:\n"
        + (done.stderr or done.stdout))
