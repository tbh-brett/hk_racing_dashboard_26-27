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

The first version of this file read only a `COLS` array, found none in six of
the eight artboards, and SKIPPED them — so it covered two pages while reporting
green on all eight, and the Trials page went on shipping without the draw or
the jockey the design asks for. `TABLELESS` below is now the only way an
artboard escapes the check, and it is a named list with a reason rather than
whatever the extractor happened to fail on.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from design_headers import header_parts, headers

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

# The page each module drives. A column header may live in the static markup
# rather than be appended by script, and both are equally rendered.
PAGE_HTML = {
    "race-day.js": "raceday.html",
    "form-guide.js": "form-guide.html",
    "lookup.js": "lookup.html",
    "bets.js": "bets.html",
    "blackbook.js": "blackbook.html",
    "results.js": "results.html",
    "trials.js": "trials.html",
    "model-analysis.js": "model-analysis.html",
}

# Labels a page renders VERBATIM from the server. The Trials page prints the
# screen read's factor names — FINISH, COMMENT, MARGIN — exactly as
# `derive/trial_quality` returns them, deliberately: writing them again in the
# browser would be a second copy of the scoring vocabulary, which is the thing
# that page's whole design is against. So the module that owns the words is
# part of what the page renders.
SERVER_LABELS = {
    "trials.js": ["hkrd/derive/trial_quality.py"],
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

# Artboards with no tabular columns to check, each with the reason. An artboard
# NOT on this list that yields no headers is a broken extractor, and the test
# says so rather than passing quietly — which is how six pages went unchecked.
TABLELESS: dict[str, str] = {
    "Model Analysis": "the lab page is panels and charts; it declares no grid "
                      "table anywhere in the artboard",
}


def _design_headers(artboard: str) -> set[str]:
    """Column headers the design declares — see tests/design_headers.py.

    Read from the declaration rather than from rendered text: the artboards
    carry sample data, and scraping every uppercase string would pick up the
    horse names and the footnotes along with the headers.
    """
    return headers(DESIGN / f"{artboard}.dc.html")


def _built_text(page: str) -> str:
    """Everything the built page renders from: its module, its helpers, and its
    own HTML.

    A static header row in the .html is as rendered as one appended in JS —
    the Blackbook writes its table head in markup and its rows in script — so
    reading only the module reported four columns missing that are on screen.
    """
    parts = [(ASSETS / page).read_text(encoding="utf-8")]
    for extra in EXTRA_SOURCES.get(page, []):
        candidate = ASSETS / extra
        if candidate.is_file():
            parts.append(candidate.read_text(encoding="utf-8"))
    html = ROOT / "web" / "pages" / PAGE_HTML.get(page, "")
    if html.is_file():
        parts.append(html.read_text(encoding="utf-8"))
    for module in SERVER_LABELS.get(page, []):
        candidate = ROOT / module
        if candidate.is_file():
            parts.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.mark.parametrize("artboard,page", sorted(PAGES.items()))
def test_every_design_column_is_ported_or_recorded(artboard: str, page: str) -> None:
    """A column in the design is in the build, or in DIVERGENCES with a reason."""
    declared = _design_headers(artboard)
    if artboard in TABLELESS:
        assert not declared, (
            f"{artboard} is listed as TABLELESS but declares {sorted(declared)}"
            " — remove it from that list.")
        return
    assert declared, (
        f"{artboard} declares no columns the extractor can read. Either it is "
        "genuinely tableless — add it to TABLELESS with the reason — or "
        "tests/design_headers.py no longer understands this artboard, in which "
        "case this page is UNCHECKED and must not pass.")

    built = _built_text(page)
    allowed = DIVERGENCES.get(artboard, {})
    missing = []
    for header in sorted(declared):
        if header in built or header in allowed:
            continue
        # The design packs several columns under one heading. Each side of the
        # middot is its own column, so each is checked on its own; a heading is
        # satisfied when every part of it is.
        # A chunk with no checkable words is a caption, not a column — "95% CI",
        # "HARVILLE-HENERY v OLD 3× RULE". Reporting those as gaps produced
        # failures for columns that are on screen, and a check that cries wolf
        # is one people start exempting. Chunks that ARE column names are still
        # required, which is what caught the missing draw and jockey.
        gaps = [chunk for chunk, words in header_parts(header)
                if words and chunk not in built
                and not all(w in built for w in words)]
        if gaps:
            missing.append(header if header == gaps[0]
                           else f"{header}  (missing: {', '.join(gaps)})")

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


def _without_comments(text: str) -> str:
    """Source with its comments removed.

    A rule that fires on the comment explaining the rule is a rule nobody can
    document.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_the_run_row_columns_are_the_order_the_owner_set() -> None:
    """The Form Guide's expanded run row, column for column.

    Not a design-artboard question — the owner set this order by hand, and it
    is the order the eye is trained on. It is also the fix for two things that
    were wrong before it: the running style appeared twice (a badge here and a
    bare initial in the trailing cell), and the race's own pace, the runner's
    ESZ and its running positions had no column at all.
    """
    text = (ASSETS / "form-guide.js").read_text(encoding="utf-8")
    head = re.search(r"const RUN_HEAD = \[(.*?)\n\];", text, re.S)
    assert head, "RUN_HEAD is no longer a literal array; this test cannot read it"
    labels = re.findall(r"\['([^']+)',", head.group(1))
    assert labels == [
        "RUN", "DATE · TRK CRS DIST GOING CL", "STYLE", "JOCKEY", "TRAINER",
        "WT", "DR", "ESZ", "POSITIONS", "FIN",
        "FIGURE · MARGIN · TIME · PLACE DIV",
        "PACE · GEAR · TRIP · BB · NOTE · VID",
    ], "the run row's columns have moved; the owner set this order"


def test_the_run_row_has_a_grid_of_its_own() -> None:
    """It shared `--fg-grid` with the runner rows, which is ten columns wide.

    Twelve columns on a ten-column template silently collapses the last two
    into one cell, which is how POSITIONS and the pace ended up crushed into
    the trailing cell in the first place.
    """
    css = (ASSETS / "formguide.css").read_text(encoding="utf-8")
    grid = re.search(r"--run-grid:\s*([^;]+);", css)
    assert grid, "formguide.css no longer defines --run-grid"
    columns = grid.group(1).split()
    assert len(columns) == 12, (
        f"--run-grid has {len(columns)} columns, RUN_HEAD has 12")
    assert re.search(r"\.run-head,\s*\.run-row\s*\{[^}]*--run-grid", css), (
        "the run rows are not using --run-grid")


def test_the_class_ladder_is_named_in_one_place() -> None:
    """"0" is a Group race and "Griffin Race" is not a class number at all.

    A page that writes `C${race_class}` renders those as "C0" and
    "CGriffin Race". `classLabel` exists so no page has to know that.
    """
    offenders = []
    for f in sorted(ASSETS.glob("*.js")):
        if f.name == VOCAB.name:
            continue
        text = _without_comments(f.read_text(encoding="utf-8"))
        if re.search(r"`C\$\{[^}]*(race_)?class", text):
            offenders.append(f.name)
    assert not offenders, (
        "these build a class label inline instead of calling classLabel: "
        + ", ".join(offenders))


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


def test_the_vet_vocabulary_is_the_same_list_on_both_sides() -> None:
    """`derive/tags.NAMED_VET` decides what a veterinary finding is; vocab.js
    decides how one is drawn. Two lists means a finding the deriver names and
    the page renders as ordinary trip trouble — which is the failure that made
    "bled" indistinguishable from "checked" in the first place.
    """
    from hkrd.derive.tags import NAMED_VET

    text = VOCAB.read_text(encoding="utf-8")
    block = re.search(r"export const VET_TAGS = new Set\((.*?)\);", text, re.S)
    assert block, "vocab.js must export VET_TAGS"
    drawn = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    # The page also draws the catch-all, which is not in NAMED_VET by design:
    # it is the fallback for a finding the vocabulary cannot name.
    assert drawn == set(NAMED_VET) | {"vet_finding"}, (
        f"only in the deriver: {set(NAMED_VET) - drawn}\n"
        f"only in the page:    {drawn - set(NAMED_VET) - {'vet_finding'}}")
