"""The columns a design artboard declares.

Split out of `test_conformance` because it is machinery, not a rule, and the
test file should read as the rules.

The artboards declare their columns two ways. Some carry a `COLS` array; most
write the header row out as literal divs. The first extractor was written
against a `COLS` array and, finding none, SKIPPED — so six of the eight pages
were never checked and the suite reported green. That is the same failure the
conformance test exists to catch, one level up: a check that cannot see a page
looks exactly like a page with nothing wrong.

So both forms are read, and an artboard that yields nothing is a failure unless
it is named as genuinely tableless.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator

__all__ = ["headers", "header_parts"]

_VOID = {"br", "img", "meta", "link", "input", "hr", "source", "col"}


class _Node:
    __slots__ = ("tag", "style", "kids", "text", "parent")

    def __init__(self, tag: str, style: str, parent: "_Node | None") -> None:
        self.tag, self.style, self.parent = tag, style, parent
        self.kids: list[_Node] = []
        self.text: list[str] = []


class _Tree(HTMLParser):
    """Just enough of a tree to find grid rows and read their cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", "", None)
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _VOID:
            return
        node = _Node(tag, dict(attrs).get("style", "") or "", self.cur)
        self.cur.kids.append(node)
        self.cur = node

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        # Unclosed tags are common in exported markup; walk up to the nearest
        # matching open rather than losing the rest of the document.
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.cur.text.append(data.strip())


def _inner(node: _Node) -> str:
    parts = list(node.text)
    parts.extend(_inner(k) for k in node.kids)
    return " ".join(p for p in parts if p)


def _labels(cell: _Node) -> list[str]:
    """The column names in one header cell.

    A cell is usually one label, but the Form Guide packs a five-column strip
    into a single grid cell as five sibling spans. Joined, that reads as one
    header nothing could ever match; split, it is the five columns it is.
    """
    named = [k for k in cell.kids if _inner(k).strip()]
    if len(named) > 1:
        return [t for k in named for t in _labels(k)]
    text = _inner(cell).strip()
    return [text] if text and "{{" not in text else []


def _grid_rows(root: _Node) -> Iterator[tuple[str, _Node]]:
    stack = [root]
    while stack:
        node = stack.pop()
        m = re.search(r"grid-template-columns:\s*([^;\"]+)", node.style)
        if m:
            yield re.sub(r"\s+", " ", m.group(1)).strip(), node
        stack.extend(node.kids)


def _from_grid(src: str) -> set[str]:
    """Header cells of every table in the artboard.

    A table is a grid template that appears more than once — once for the
    header, once for the row the data repeats through. The header is the
    occurrence whose cells are literal text; a data row is all `{{ }}`.
    """
    tree = _Tree()
    tree.feed(src)
    by_template: dict[str, list[_Node]] = {}
    for template, node in _grid_rows(tree.root):
        by_template.setdefault(template, []).append(node)

    out: set[str] = set()
    for nodes in by_template.values():
        if len(nodes) < 2:
            continue
        for node in nodes:
            whole = _inner(node)
            if not whole or "{{" in whole:
                continue
            cells = [c for k in node.kids for c in _labels(k)]
            # Two literal cells is a caption, not a table header.
            if len(cells) >= 3:
                out |= set(cells)
            break
    return out


def _from_cols_array(src: str) -> set[str]:
    """Artboards that declare a `COLS` array instead of writing the row out."""
    out: set[str] = set()
    for block in re.findall(r"const COLS\w* = \[(.*?)\n\s*\];", src, re.S):
        out |= {m.group(1) for m in re.finditer(r"""t:\s*['"]([^'"]+)['"]""", block)}
    return {h.strip() for h in out if h.strip()}


def headers(path: Path) -> set[str]:
    """Every column header the artboard declares, by either route."""
    src = path.read_text(encoding="utf-8")
    return _from_cols_array(src) | _from_grid(src)


def header_parts(header: str) -> Iterator[tuple[str, list[str]]]:
    """A packed header, split into the column names it actually contains.

    The design writes several columns under one heading — `POSITIONS · TRIP`,
    `FIGURE · MARGIN · TIME · PLACE DIV`. The middot is its own separator, so
    each side is checked on its own. Yields the chunk and the capitalised words
    in it, which is what a build can be expected to name.
    """
    for chunk in re.split(r"\s*·\s*", header):
        chunk = chunk.strip()
        if not chunk:
            continue
        words = [w for w in chunk.split() if _is_column_word(w)]
        # A chunk is only a column name when EVERY token in it is one. "95% CI"
        # is a caption; keeping its `CI` would have made the check pass on any
        # page that happened to contain those two letters, which is worse than
        # not checking it — a green that means nothing.
        yield chunk, (words if len(words) == len(chunk.split()) else [])


def _is_column_word(word: str) -> bool:
    """A capitalised word a build could plausibly render as a column header."""
    return len(word) >= 2 and word.isalpha() and word.upper() == word
