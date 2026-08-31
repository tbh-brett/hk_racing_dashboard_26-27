"""Does the built page render what the design declares?

Nothing detected an incomplete port. The canvas export in `web/design-source/`
was ported by hand, about two thirds of it arrived, and the missing third was
invisible until someone put the two screenshots side by side — which is a
memory exercise, run once, by a person.

This makes it a test. For each page the design's declared column headers are
extracted from its `.dc.html` and checked against the built page's assets. A
header in the design and not in the build is either a fix or a recorded
decision in `DIVERGENCES` below, and never a third thing.

`DIVERGENCES` is the deliberate part. A design and a build are allowed to
disagree — the design predates measurements that changed decisions — but the
disagreement has to be written down with its reason, or it is indistinguishable
from an omission. Adding an entry is cheap; adding one without a reason should
feel wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "web" / "design-source"
ASSETS = ROOT / "web" / "assets"

# design artboard -> the page module that ports it
PAGES = {
    "Race Day": "race-day.js",
    "Form Guide": "form-guide.js",
    "Lookup": "lookup.js",
    "Bets": "bets.js",
    "Blackbook": "blackbook.js",
    "Results": "results.js",
    "Trials": "trials.js",
    "Model Analysis": "model-analysis.js",
}

# A page's own module plus anything it imports for rendering. A header may be
# ported into a helper rather than the page file itself.
EXTRA_SOURCES = {
    "bets.js": ["bets-entry.js"],
    "form-guide.js": ["review.js"],
    "results.js": ["review.js"],
    "blackbook.js": ["review.js"],
}

# ─── deliberate divergences ───────────────────────────────────────────────────
# page -> {design header: why the build does not carry it}
# Every entry is a decision someone made on purpose, with the reason attached.
DIVERGENCES: dict[str, dict[str, str]] = {
    # Empty, and that is the point: every column the design declares is
    # currently rendered. When one is deliberately dropped, it goes here with
    # the reason, e.g.
    #     "Race Day": {"SOME COLUMN": "dropped because <measurement>"},
}


def _design_headers(artboard: str) -> set[str]:
    """Column headers the design declares, from its own COLS array.

    Read from the declaration rather than from rendered text: the artboards
    carry sample data, and scraping every uppercase string would pick up the
    horse names and the footnotes along with the headers.
    """
    src = (DESIGN / f"{artboard}.dc.html").read_text(encoding="utf-8")
    out: set[str] = set()
    for block in re.findall(r"const COLS = \[(.*?)\n\];", src, re.S):
        out |= {m.group(1) for m in re.finditer(r"t:\s*'([^']+)'", block)}
        out |= {m.group(1) for m in re.finditer(r't:\s*"([^"]+)"', block)}
    return {h.strip() for h in out if h.strip()}


def _built_text(page: str) -> str:
    parts = [(ASSETS / page).read_text(encoding="utf-8")]
    for extra in EXTRA_SOURCES.get(page, []):
        candidate = ASSETS / extra
        if candidate.is_file():
            parts.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.mark.parametrize("artboard,page", sorted(PAGES.items()))
def test_every_design_column_is_ported_or_recorded(artboard: str, page: str) -> None:
    """A column in the design is in the build, or in DIVERGENCES with a reason."""
    declared = _design_headers(artboard)
    if not declared:
        pytest.skip(f"{artboard} declares no COLS array")

    built = _built_text(page)
    allowed = DIVERGENCES.get(artboard, {})
    missing = []
    for header in sorted(declared):
        if header in built or header in allowed:
            continue
        missing.append(header)

    assert not missing, (
        f"{artboard} declares columns the built {page} does not render:\n"
        + "\n".join(f"  · {h}" for h in missing)
        + "\n\nEither port them, or add each to DIVERGENCES in this file with "
          "the reason it is deliberate. An undocumented gap is how two thirds "
          "of a design shipped as the whole of one.")


def test_every_divergence_names_a_page_that_exists() -> None:
    """A stale exemption silently re-opens the gap it was written for."""
    unknown = set(DIVERGENCES) - set(PAGES)
    assert not unknown, f"DIVERGENCES names artboards that do not exist: {unknown}"


def test_every_divergence_is_still_needed() -> None:
    """An exemption for a column that IS built is an exemption nobody removed.

    Left in place, it would go on excusing that column if it were later dropped.
    """
    stale = []
    for artboard, entries in DIVERGENCES.items():
        built = _built_text(PAGES[artboard])
        declared = _design_headers(artboard)
        for header in entries:
            if header in built:
                stale.append(f"{artboard}: '{header}' is built — remove the exemption")
            elif declared and header not in declared:
                stale.append(
                    f"{artboard}: '{header}' is not in the design any more")
    assert not stale, "\n".join(stale)


def test_every_divergence_gives_a_reason() -> None:
    """A one-word exemption is an omission with a label on it."""
    thin = [f"{a}: {h}" for a, entries in DIVERGENCES.items()
            for h, why in entries.items() if len(why.split()) < 5]
    assert not thin, ("these exemptions do not say why:\n"
                      + "\n".join(f"  · {t}" for t in thin))


# ─── the shared vocabulary ────────────────────────────────────────────────────

VOCAB = ASSETS / "vocab.js"


def test_no_page_redeclares_the_shared_vocabulary() -> None:
    """The nav array lived in nine files and was wrong in all nine.

    `RunnerLine` makes a run the same object everywhere; this keeps it the same
    RENDERING everywhere, which is the half that had drifted.
    """
    shared = ("DASH", "MINUS", "el", "$", "NAV")
    offenders = []
    for f in sorted(ASSETS.glob("*.js")):
        if f.name == VOCAB.name:
            continue
        text = f.read_text(encoding="utf-8")
        for name in shared:
            if re.search(rf"^const {re.escape(name)} = ", text, re.M):
                offenders.append(f"{f.name}: redeclares {name}")
    assert not offenders, (
        "import these from vocab.js instead:\n" + "\n".join(offenders))


def test_the_navigation_is_defined_once_and_in_order() -> None:
    """Design note 11 §4, plus Model Analysis last — the owner's decision."""
    text = VOCAB.read_text(encoding="utf-8")
    order = re.findall(r"\['([^']+)', '([^']+\.html)'\]", text)
    assert [name for name, _ in order] == [
        "Race Day", "Form Guide", "Bets", "Blackbook",
        "Results", "Lookup", "Trials", "Model Analysis"]


def test_every_page_the_nav_points_at_exists() -> None:
    """A nav entry to a page that is not there is a dead link on every screen."""
    text = VOCAB.read_text(encoding="utf-8")
    hrefs = re.findall(r"\['[^']+', '([^']+\.html)'\]", text)
    missing = [h for h in hrefs if not (ROOT / "web" / "pages" / h).is_file()]
    assert not missing, f"nav points at missing pages: {missing}"


def test_running_style_is_rendered_through_one_function() -> None:
    """Lookup showed style as plain text while every other page showed a badge.

    Same run, same data, different reading depending on where you looked.
    """
    offenders = []
    for f in sorted(ASSETS.glob("*.js")):
        if f.name == VOCAB.name:
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"style-\$\{|`style style-|'style-chip style-", text):
            offenders.append(f.name)
    assert not offenders, (
        "these build a style class inline instead of calling styleBadge/"
        f"styleClass from vocab.js: {offenders}")
