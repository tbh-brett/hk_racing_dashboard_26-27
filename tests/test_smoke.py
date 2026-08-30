"""Smoke test — the architecture rules in AGENTS.md, enforced as code.

This must pass before any commit. It is deliberately mechanical: every rule here
maps to a specific way the previous codebase went wrong, and the point is to catch
the regression on the commit that introduces it rather than months later.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "hkrd"
WEB = ROOT / "web"

PACKAGES = [
    "hkrd", "hkrd.ingest", "hkrd.store", "hkrd.derive",
    "hkrd.model", "hkrd.query", "hkrd.api", "hkrd.jobs",
]
LAYERS = ("ingest", "store", "derive", "model", "query", "api", "jobs")

# jobs/migrate_legacy.py is the one sanctioned reader of the legacy database.
LEGACY_READER = "jobs/migrate_legacy.py"


def py_files() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def rel(p: Path) -> str:
    return p.relative_to(PKG).as_posix()


def parse(f: Path) -> ast.Module:
    return ast.parse(f.read_text(encoding="utf-8"))


def code_only(f: Path) -> str:
    """Source with comments and docstrings blanked out.

    The pattern guards below hunt for forbidden constructs. Documenting one --
    naming `p / sum(p) * 3` in a docstring so the next reader knows why the
    module exists -- is the opposite of committing it, and must not trip the
    guard.

    Only comments and DOCSTRINGS are removed, never ordinary string literals:
    the lbw and horse_id guards match column names, which live inside strings
    like df["lbw"]. Blanking every literal would silently defang them, which is
    a worse failure than the false positive being fixed. Blanking rather than
    deleting keeps line numbers intact.
    """
    import io
    import tokenize

    src = f.read_text(encoding="utf-8")
    out = src.splitlines(keepends=True)

    def blank(r1: int, c1: int, r2: int, c2: int) -> None:
        for row in range(r1 - 1, min(r2, len(out))):
            line = out[row]
            a = c1 if row == r1 - 1 else 0
            b = c2 if row == r2 - 1 else len(line.rstrip("\n"))
            out[row] = line[:a] + " " * max(0, b - a) + line[b:]

    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                blank(*tok.start, *tok.end)
    except (tokenize.TokenError, IndentationError):
        return src

    # Docstrings: a bare string expression opening a module, class or function.
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "".join(out)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            blank(first.lineno, first.col_offset,
                  first.end_lineno or first.lineno, first.end_col_offset or 0)
    return "".join(out)


def imported_roots(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".")[0]]
    return []


# ─── structure ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mod", PACKAGES)
def test_every_package_imports(mod: str) -> None:
    importlib.import_module(mod)


def test_layer_directories_exist() -> None:
    for layer in LAYERS:
        assert (PKG / layer / "__init__.py").is_file(), f"missing hkrd/{layer}/__init__.py"


# ─── the import direction: ingest -> store -> derive -> query -> api -> web ───

def test_only_store_imports_sqlite3() -> None:
    """store/ is the only module that may talk to the database."""
    offenders = []
    for f in py_files():
        name = rel(f)
        for node in ast.walk(parse(f)):
            for m in imported_roots(node):
                if m == "sqlite3" and not (name.startswith("store/") or name == LEGACY_READER):
                    offenders.append(f"{name}: imports sqlite3 outside store/")
    assert not offenders, "\n".join(offenders)


def test_api_imports_only_query_and_jobs() -> None:
    """Routers read through query/ and trigger work through jobs/.

    Nothing else: reaching into store/ or derive/ from a router puts SQL and
    formula knowledge behind an HTTP handler, which is how the previous
    dashboard ended up with 152 places that each knew how to load data.
    """
    forbidden = {"ingest", "store", "derive"}
    offenders = []
    for f in py_files():
        if not rel(f).startswith("api/"):
            continue
        for node in ast.walk(parse(f)):
            for m in imported_roots(node):
                if m in {"requests", "subprocess", "sqlite3"}:
                    offenders.append(f"{rel(f)}: api/ may not import {m}")
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "hkrd" and len(parts) > 1 and parts[1] in forbidden:
                    offenders.append(f"{rel(f)}: api/ may not import hkrd.{parts[1]}")
    assert not offenders, "\n".join(offenders)


def test_api_returns_json_not_html() -> None:
    """Routers serialise data. The Design HTML in web/ is the only place markup lives."""
    offenders = []
    for f in py_files():
        if not rel(f).startswith("api/"):
            continue
        src = f.read_text(encoding="utf-8")
        for bad in ("HTMLResponse", "Jinja2Templates", "render_template"):
            if bad in src:
                offenders.append(f"{rel(f)}: api/ may not build HTML ({bad})")
    assert not offenders, "\n".join(offenders)


def test_web_is_static() -> None:
    """web/ is the Design output. No Python, no database, no secrets."""
    if not WEB.is_dir():
        pytest.skip("web/ not populated yet")
    stray = [p.relative_to(ROOT).as_posix() for p in WEB.rglob("*.py")]
    assert not stray, f"web/ must contain no Python: {stray}"


def test_no_subprocess_call_to_our_own_python() -> None:
    """subprocess is reserved for Playwright. 29 sites in the old repo cost 0.61s each."""
    offenders = []
    for f in py_files():
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(run|Popen|call|check_output)\s*\(([^)]{0,200})", src):
            if "playwright" not in m.group(2).lower():
                offenders.append(f"{rel(f)}:{src[: m.start()].count(chr(10)) + 1}: subprocess not for Playwright")
    assert not offenders, "\n".join(offenders)


# ─── the bugs that must never come back ───────────────────────────────────────

def test_no_silent_except() -> None:
    """66 `except: pass` blocks in the old dashboard.py are why bugs hid for months."""
    offenders = []
    for f in py_files():
        for node in ast.walk(parse(f)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                offenders.append(f"{rel(f)}:{node.lineno}: except -> pass")
            elif node.type is None:
                offenders.append(f"{rel(f)}:{node.lineno}: bare except")
    assert not offenders, "\n".join(offenders)


def test_no_to_numeric_on_lbw() -> None:
    """pd.to_numeric drops 79.1% of lbw values — they are fractions like '3-1/4'."""
    pat = re.compile(r"to_numeric\s*\([^)]*lbw", re.I)
    bad = [rel(f) for f in py_files() if pat.search(code_only(f))]
    assert not bad, f"pd.to_numeric on lbw: {bad}"


def test_no_linear_place_probability() -> None:
    """p / sum(p) * 3 overstates the banker by ~34 points. Use Harville-Henery."""
    pat = re.compile(r"/\s*(np\.)?sum\([^)]*\)\s*\*\s*3\b")
    bad = [rel(f) for f in py_files() if pat.search(code_only(f))]
    assert not bad, f"linear place-probability transform: {bad}"


def test_no_join_on_horse_id() -> None:
    """horse_id is 0% populated from July 2026 and degrading from April. Join horse_name."""
    pat = re.compile(r"(on|by|left_on|right_on)\s*=\s*[\"']horse_id[\"']|JOIN[^\n]*horse_id", re.I)
    bad = [rel(f) for f in py_files() if pat.search(code_only(f))]
    assert not bad, f"join on horse_id: {bad}"


def test_no_read_excel_outside_migrate_legacy() -> None:
    """read_excel cost 15.33s per form-guide call for data already in the database."""
    bad = [rel(f) for f in py_files()
           if "read_excel" in code_only(f) and rel(f) != LEGACY_READER]
    assert not bad, f"read_excel outside {LEGACY_READER}: {bad}"


def test_no_snapshot_pruning() -> None:
    """Odds history must never be deleted — 17 meetings survived a full season."""
    bad = [rel(f) for f in py_files() if "prune_old_snapshots" in code_only(f)]
    assert not bad, f"snapshot pruning: {bad}"


# ─── file size ────────────────────────────────────────────────────────────────

def test_no_file_over_600_lines() -> None:
    over = [f"{rel(f)}: {n} lines" for f in py_files()
            if (n := len(f.read_text(encoding="utf-8").splitlines())) > 600]
    assert not over, "hard cap is 600 lines:\n" + "\n".join(over)


# ─── the design layer ─────────────────────────────────────────────────────────

WEB_ASSETS = ROOT / "web" / "assets"
TOKENS = WEB_ASSETS / "tokens.css"
DESIGN_SOURCE = ROOT / "web" / "design-source"


def web_sources() -> list[Path]:
    """Ported page and asset files. design-source/ is the untouched export."""
    if not WEB.is_dir():
        return []
    return [p for p in sorted(WEB.rglob("*"))
            if p.suffix in {".html", ".css", ".js"}
            and DESIGN_SOURCE not in p.parents
            and p != TOKENS]


def test_no_raw_hex_outside_tokens() -> None:
    """One colour, one meaning, everywhere.

    The design export carried 126 distinct hex values across 1,823 inline style
    attributes with no classes -- 71 neutrals collapsing to 12 real steps. That
    rule is only enforceable if colours have names, so tokens.css is the only
    file allowed to contain one.
    """
    pat = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    offenders = []
    for f in web_sources():
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and "tokens.css" not in line:
                offenders.append(f"{f.relative_to(ROOT)}:{i}: raw hex -- use a token")
    assert not offenders, "\n".join(offenders[:40])


def test_tokens_file_exists_and_defines_the_palette() -> None:
    assert TOKENS.is_file(), "web/assets/tokens.css is the single colour vocabulary"
    src = TOKENS.read_text(encoding="utf-8")
    required = [
        "--bg", "--surface", "--text", "--edge", "--book", "--alert",
        "--style-leader", "--style-onpace", "--style-midfield", "--style-closer",
    ]
    missing = [t for t in required if f"{t}:" not in src]
    assert not missing, f"tokens.css missing: {missing}"


def test_running_style_colours_are_distinct() -> None:
    """Brief 05 §2: four distinct hues, not brightness steps.

    The failure mode the brief names is four colours at one hue separated only
    by lightness -- the eye reads that as one thing at four intensities, not as
    four categories. Hue distance alone is the wrong test, because Midfield is
    deliberately near-neutral ("the calmest of the four"): it sits 15 degrees
    from Closer in hue but 53 points apart in saturation, and the two are
    obviously different colours.

    So measure separation in the hue-saturation plane, treating them as polar
    coordinates. A brightness ramp collapses to ~0 there and fails; a genuinely
    varied set does not.
    """
    import colorsys
    from math import cos, sin, radians, hypot

    src = TOKENS.read_text(encoding="utf-8")
    pts = {}
    for name in ("leader", "onpace", "midfield", "closer"):
        m = re.search(rf"--style-{name}:\s*#([0-9a-fA-F]{{6}})", src)
        assert m, f"--style-{name} not defined"
        r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, _, s = colorsys.rgb_to_hls(r, g, b)
        deg, sat = h * 360, s * 100
        pts[name] = (sat * cos(radians(deg)), sat * sin(radians(deg)))

    too_close = []
    names = list(pts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
            if d < 25:
                too_close.append(f"  {a} and {b}: separation {d:.0f} (need 25)")
    assert not too_close, (
        "running styles must be four distinct colours, not one hue at four "
        "brightnesses:\n" + "\n".join(too_close)
    )


def test_page_stylesheets_do_not_share_a_top_level_class() -> None:
    """A class two page stylesheets both style unqualified is a live collision.

    raceday.css styled `.detail` as a 256px fixed aside. formguide.css used the
    same name for the block under an expanded horse, and the Form Guide loaded
    raceday.css for the race strip — so every expanded horse silently rendered
    256px wide. Nothing errored; the page was simply wrong.

    Only the LEADING class of a selector counts. `.band-item .name` and
    `.fit-cell .name` are scoped by their ancestor and cannot collide; `.detail`
    and `.detail` can. Shared rules belong in pages.css, which is exempt.

    Every page stylesheet belongs in this list. blackbook.css was left out of
    it, and the filter bar, chips and panel frames it defined were about to be
    written a second time for the Bets page — the same collision, one sheet
    later. They are in pages.css now, painted from page-scoped tokens.
    """
    page_sheets = ["raceday.css", "formguide.css", "model.css",
                   "blackbook.css", "bets.css", "lookup.css",
                   "trials.css", "results.css"]
    seen: dict[str, list[str]] = {}
    for sheet in page_sheets:
        path = WEB_ASSETS / sheet
        if not path.is_file():
            continue
        src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        for block in re.findall(r"([^{}]+)\{", src):
            for selector in block.split(","):
                head = selector.strip().split()[0] if selector.strip() else ""
                m = re.match(r"^\.([a-z][a-z0-9-]*)", head)
                if m and sheet not in seen.setdefault(m.group(1), []):
                    seen[m.group(1)].append(sheet)

    clashes = sorted(f"  .{name}: {' and '.join(sheets)}"
                     for name, sheets in seen.items() if len(sheets) > 1)
    assert not clashes, (
        "these classes are styled unqualified in more than one page "
        "stylesheet; move them to pages.css or rename:\n" + "\n".join(clashes)
    )


def test_no_rule_paints_text_on_its_own_colour() -> None:
    """color and background-color set to the same value make text invisible.

    Written twice in one sitting, from grouping a text selector and a bar-fill
    selector into one rule: `.t.fast, .bar i.fast { color: X; background: X }`
    gives the bar its fill and the number a ground it vanishes into. Nothing
    errors — the value is simply not on screen.
    """
    offenders = []
    for sheet in sorted(WEB_ASSETS.glob("*.css")):
        src = re.sub(r"/\*.*?\*/", "", sheet.read_text(encoding="utf-8"), flags=re.S)
        for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", src):
            decls = dict(
                (k.strip(), v.strip())
                for k, _, v in (d.partition(":") for d in block.split(";")) if v)
            fg = decls.get("color")
            bg = decls.get("background") or decls.get("background-color")
            # A shorthand `background` can carry more than a colour; compare the
            # first token, which is where a bare colour would sit.
            if fg and bg and fg == bg.split()[0] and fg not in ("inherit", "currentColor"):
                offenders.append(f"  {sheet.name}: {selector.strip()} — {fg}")
    assert not offenders, (
        "these rules paint text on a ground of its own colour:\n"
        + "\n".join(offenders)
    )


def test_every_route_module_is_included_in_the_app() -> None:
    """A router nobody includes is a page of 404s that nothing catches.

    app.py grew past the 600-line cap and was split by domain; the failure
    mode a split introduces is a module that exists, imports cleanly, and is
    never mounted.
    """
    from hkrd.api import routes
    from hkrd.api.app import app

    # This FastAPI version wraps an included router in an opaque object rather
    # than copying its routes onto the app, so the app's own list does not
    # contain them. The OpenAPI schema is the one place that always names every
    # path the app will actually serve.
    mounted = set(app.openapi()["paths"])

    missing = []
    for name in routes.__all__:
        module = getattr(routes, name)
        for route in module.router.routes:
            if route.path not in mounted:
                missing.append(f"{name}: {route.path}")
    assert not missing, "declared but never mounted:\n" + "\n".join(missing)


def test_a_literal_path_is_declared_before_the_parameter_that_would_eat_it() -> None:
    """`/api/blackbook/{entry_id}` matches `/api/blackbook/tags`.

    FastAPI resolves in declaration order, so a literal segment declared after
    a path parameter that can match it is unreachable — `tags` gets looked up
    as an entry id and 404s. This has to be a test rather than a comment,
    because the breakage is silent at import time and only shows as one route
    quietly answering for another.

    Two paths can only collide when they have the SAME number of segments and
    differ only where one holds a parameter: `/api/blackbook/{entry_id}/status`
    cannot shadow `/api/blackbook/backed-vs-missed`, which is a segment
    shorter.
    """
    import re

    from hkrd.api import routes

    param = re.compile(r"\A\{[^}]+\}\Z")

    def shadows(pattern: list[str], literal: list[str]) -> bool:
        """True when `pattern` would match a request for `literal`."""
        if len(pattern) != len(literal):
            return False
        return all(param.fullmatch(a) or a == b
                   for a, b in zip(pattern, literal))

    problems = []
    for name in routes.__all__:
        declared: list[tuple[str, list[str], set[str]]] = []
        for route in getattr(routes, name).router.routes:
            parts = route.path.strip("/").split("/")
            methods = set(route.methods or ())
            for earlier, earlier_parts, earlier_methods in declared:
                if not (methods & earlier_methods):
                    continue          # different verbs never shadow
                if any(param.fullmatch(x) for x in parts):
                    continue          # only a literal path can be eaten
                if shadows(earlier_parts, parts):
                    problems.append(
                        f"{name}: {route.path} is declared after {earlier}, "
                        f"which matches it — the literal is unreachable")
            declared.append((route.path, parts, methods))
    assert not problems, "\n".join(problems)


def test_no_two_routes_claim_the_same_path_and_method() -> None:
    """FastAPI answers with the FIRST route registered for a path.

    A second `@app.get("/api/status")` does not raise, does not warn where
    anyone reads it, and does not 500. It simply never runs, and the endpoint
    someone believes they added quietly returns the other one's payload — the
    exact shape of failure this rebuild exists to remove. It happened once
    already: a deployment status endpoint was added beside the model's, and
    the model's was the one that had been there first.

    This walks the whole application, mounts and routers included, rather than
    the router modules, because the collision that occurred was between a
    router and app.py.
    """
    from collections import Counter

    from hkrd.api.app import app

    seen: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            if method in ("HEAD", "OPTIONS"):
                continue
            seen[(method, getattr(route, "path", ""))] += 1

    duplicates = [f"{m} {path} declared {n} times"
                  for (m, path), n in sorted(seen.items()) if n > 1]
    assert not duplicates, (
        "only the first declaration answers:\n" + "\n".join(duplicates))


def test_the_promotion_form_is_written_once() -> None:
    """The Results artboard asks for "same form as the Form Guide — reviewing
    and booking is one action". Two copies would give the two surfaces
    different tag vocabularies and eventually different rules about what a
    promotion means, which is the shape the old dashboard was in.
    """
    import re

    assets = WEB_ASSETS
    calls = {}
    for path in sorted(assets.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        # api.js declares the endpoint; review.js is the only caller.
        hits = len(re.findall(r"\bapi\.createBlackbookEntry\(", src))
        if hits:
            calls[path.name] = hits
    assert list(calls) == ["review.js"], (
        "the blackbook promotion is called from more than one place: "
        f"{sorted(calls)}")
