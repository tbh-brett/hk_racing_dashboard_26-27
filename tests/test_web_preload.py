"""Every page names its whole module graph up front (`<link rel="modulepreload">`).

Without the list a browser finds a page's modules one import level at a time:
the Briefing's entry script, then the nine it imports, then the four those
import -- three round trips to Singapore in series, each a revalidation
(`api/app._RevalidatingStatic`), before the page can ask for any data. With
it, all of them are asked for together as the page opens.

The list is written by hand into each page, so this keeps it true: a module
added or dropped without its line fails here, and the message is the block to
paste. Dynamic `import()` is left out on purpose -- a lazy module is lazy.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
_STATIC = re.compile(r"""^\s*(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]\./([\w.-]+\.js)['"]""",
                     re.M)
_ENTRY = re.compile(r'<script type="module" src="\.\./assets/([\w.-]+\.js)"></script>')
_LINK = re.compile(r'<link rel="modulepreload" href="\.\./assets/([\w.-]+\.js)">')


def graph(entry: str) -> set[str]:
    """Every module `entry` reaches through static imports, itself included."""
    seen, todo = set(), [entry]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(_STATIC.findall((WEB / "assets" / name).read_text(encoding="utf-8")))
    return seen


def pages() -> list[Path]:
    return sorted(p for p in (WEB / "pages").glob("*.html")
                  if _ENTRY.search(p.read_text(encoding="utf-8")))


def test_every_module_page_is_checked():
    assert len(pages()) >= 9


def test_each_page_preloads_exactly_its_modules():
    wrong = []
    for page in pages():
        html = page.read_text(encoding="utf-8")
        want = graph(_ENTRY.search(html).group(1))
        got = set(_LINK.findall(html))
        if got != want:
            block = "\n".join(f'<link rel="modulepreload" href="../assets/{m}">'
                              for m in sorted(want))
            wrong.append(f"{page.name}: missing {sorted(want - got)}, extra "
                         f"{sorted(got - want)}; the block is\n{block}")
    assert not wrong, "\n\n".join(wrong)


def test_the_list_is_in_the_head():
    """In the body it would be found no sooner than the entry script itself."""
    for page in pages():
        html = page.read_text(encoding="utf-8")
        if _LINK.search(html):
            assert html.index("modulepreload") < html.index("</head>"), page.name
