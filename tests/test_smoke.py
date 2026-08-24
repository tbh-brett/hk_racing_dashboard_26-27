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

PACKAGES = [
    "hkrd", "hkrd.ingest", "hkrd.store", "hkrd.derive",
    "hkrd.model", "hkrd.query", "hkrd.ui", "hkrd.jobs",
]

# jobs/migrate_legacy.py is the one sanctioned reader of the legacy SQLite file.
LEGACY_READER = "jobs/migrate_legacy.py"


def py_files() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def rel(p: Path) -> str:
    return p.relative_to(PKG).as_posix()


# ─── structure ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mod", PACKAGES)
def test_every_package_imports(mod: str) -> None:
    importlib.import_module(mod)


def test_layer_directories_exist() -> None:
    for layer in ("ingest", "store", "derive", "model", "query", "ui", "jobs"):
        assert (PKG / layer / "__init__.py").is_file(), f"missing hkrd/{layer}/__init__.py"


# ─── the import direction: ingest -> store -> derive -> query -> ui ───────────

def test_only_store_imports_a_db_driver() -> None:
    """store/ is the only module that may import psycopg.

    jobs/migrate_legacy.py may import sqlite3 to read the legacy file, and nothing else may.
    """
    offenders = []
    for f in py_files():
        src = f.read_text(encoding="utf-8")
        name = rel(f)
        for tree in [ast.parse(src)]:
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    if m == "psycopg" and not name.startswith("store/"):
                        offenders.append(f"{name}: imports psycopg outside store/")
                    if m == "sqlite3" and name != LEGACY_READER:
                        offenders.append(f"{name}: imports sqlite3 (only {LEGACY_READER} may)")
    assert not offenders, "\n".join(offenders)


def test_ui_imports_only_query() -> None:
    forbidden = {"ingest", "store", "derive", "model", "jobs"}
    offenders = []
    for f in py_files():
        if not rel(f).startswith("ui/"):
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = node.names[0].name
            if not mod:
                continue
            parts = mod.split(".")
            if parts[0] == "hkrd" and len(parts) > 1 and parts[1] in forbidden:
                offenders.append(f"{rel(f)}: ui/ may not import hkrd.{parts[1]}")
            if parts[0] in {"requests", "subprocess"}:
                offenders.append(f"{rel(f)}: ui/ may not import {parts[0]}")
    assert not offenders, "\n".join(offenders)


def test_no_subprocess_call_to_our_own_python() -> None:
    """subprocess is reserved for Playwright. 29 sites in the old repo cost 0.61s each."""
    offenders = []
    for f in py_files():
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(run|Popen|call|check_output)\s*\(([^)]{0,200})", src):
            args = m.group(2)
            if "playwright" not in args.lower():
                line = src[: m.start()].count("\n") + 1
                offenders.append(f"{rel(f)}:{line}: subprocess not for Playwright")
    assert not offenders, "\n".join(offenders)


# ─── the bugs that must never come back ───────────────────────────────────────

def test_no_silent_except() -> None:
    """66 `except: pass` blocks in the old dashboard.py are why bugs hid for months."""
    offenders = []
    for f in py_files():
        src = f.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                offenders.append(f"{rel(f)}:{node.lineno}: except -> pass")
            elif node.type is None:
                offenders.append(f"{rel(f)}:{node.lineno}: bare except")
    assert not offenders, "\n".join(offenders)


def test_no_to_numeric_on_lbw() -> None:
    """pd.to_numeric drops 79.1% of lbw values — they are fractions like '3-1/4'."""
    pat = re.compile(r"to_numeric\s*\([^)]*lbw", re.I)
    offenders = [f"{rel(f)}" for f in py_files() if pat.search(f.read_text(encoding="utf-8"))]
    assert not offenders, f"pd.to_numeric on lbw: {offenders}"


def test_no_linear_place_probability() -> None:
    """p / sum(p) * 3 overstates the banker by ~34 points. Use Harville-Henery."""
    pat = re.compile(r"/\s*(np\.)?sum\([^)]*\)\s*\*\s*3\b")
    offenders = [f"{rel(f)}" for f in py_files() if pat.search(f.read_text(encoding="utf-8"))]
    assert not offenders, f"linear place-probability transform: {offenders}"


def test_no_join_on_horse_id() -> None:
    """horse_id is 0% populated from July 2026 and degrading from April. Join horse_name."""
    pat = re.compile(r"(on|by|left_on|right_on)\s*=\s*[\"']horse_id[\"']|JOIN[^\n]*horse_id", re.I)
    offenders = [f"{rel(f)}" for f in py_files() if pat.search(f.read_text(encoding="utf-8"))]
    assert not offenders, f"join on horse_id: {offenders}"


def test_no_read_excel_outside_migrate_legacy() -> None:
    """read_excel cost 15.33s per form-guide call for data already in the database."""
    offenders = [
        rel(f) for f in py_files()
        if "read_excel" in f.read_text(encoding="utf-8") and rel(f) != LEGACY_READER
    ]
    assert not offenders, f"read_excel outside {LEGACY_READER}: {offenders}"


def test_no_snapshot_pruning() -> None:
    """Odds history must never be deleted — 17 meetings survived a full season."""
    offenders = [rel(f) for f in py_files() if "prune_old_snapshots" in f.read_text(encoding="utf-8")]
    assert not offenders, f"snapshot pruning: {offenders}"


# ─── file size ────────────────────────────────────────────────────────────────

def test_no_file_over_600_lines() -> None:
    over = []
    for f in py_files():
        n = len(f.read_text(encoding="utf-8").splitlines())
        if n > 600:
            over.append(f"{rel(f)}: {n} lines")
    assert not over, "hard cap is 600 lines:\n" + "\n".join(over)
