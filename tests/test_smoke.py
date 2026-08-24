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


def test_api_imports_only_query() -> None:
    """Routers reach the data through query/ and nowhere else."""
    forbidden = {"ingest", "store", "derive", "model", "jobs"}
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
    bad = [rel(f) for f in py_files() if pat.search(f.read_text(encoding="utf-8"))]
    assert not bad, f"pd.to_numeric on lbw: {bad}"


def test_no_linear_place_probability() -> None:
    """p / sum(p) * 3 overstates the banker by ~34 points. Use Harville-Henery."""
    pat = re.compile(r"/\s*(np\.)?sum\([^)]*\)\s*\*\s*3\b")
    bad = [rel(f) for f in py_files() if pat.search(f.read_text(encoding="utf-8"))]
    assert not bad, f"linear place-probability transform: {bad}"


def test_no_join_on_horse_id() -> None:
    """horse_id is 0% populated from July 2026 and degrading from April. Join horse_name."""
    pat = re.compile(r"(on|by|left_on|right_on)\s*=\s*[\"']horse_id[\"']|JOIN[^\n]*horse_id", re.I)
    bad = [rel(f) for f in py_files() if pat.search(f.read_text(encoding="utf-8"))]
    assert not bad, f"join on horse_id: {bad}"


def test_no_read_excel_outside_migrate_legacy() -> None:
    """read_excel cost 15.33s per form-guide call for data already in the database."""
    bad = [rel(f) for f in py_files()
           if "read_excel" in f.read_text(encoding="utf-8") and rel(f) != LEGACY_READER]
    assert not bad, f"read_excel outside {LEGACY_READER}: {bad}"


def test_no_snapshot_pruning() -> None:
    """Odds history must never be deleted — 17 meetings survived a full season."""
    bad = [rel(f) for f in py_files() if "prune_old_snapshots" in f.read_text(encoding="utf-8")]
    assert not bad, f"snapshot pruning: {bad}"


# ─── file size ────────────────────────────────────────────────────────────────

def test_no_file_over_600_lines() -> None:
    over = [f"{rel(f)}: {n} lines" for f in py_files()
            if (n := len(f.read_text(encoding="utf-8").splitlines())) > 600]
    assert not over, "hard cap is 600 lines:\n" + "\n".join(over)
