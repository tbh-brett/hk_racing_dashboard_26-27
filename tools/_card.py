"""The meeting a payload is built against: the roster, and how to find each
runner's name in running Chinese text.

Shared, so every source resolves against the same closed list of runners —
the card the DASHBOARD holds, which is the card the import will check the
numbers against. English name-finding is `_scan.english_mentions`.
"""
from __future__ import annotations

from typing import Any

__all__ = ["Card", "EXACT", "FRAGMENT"]

EXACT, FRAGMENT = 0.95, 0.80


class Card:
    """The roster, and how to find each runner's name in running text."""

    def __init__(self, roster: dict[str, Any]) -> None:
        self.runners = [dict(x, race_no=r["race_no"])
                        for r in roster["races"] for x in r["runners"]]
        self.named = [x for x in self.runners if x["name_zh"]]
        all_zh = [x["name_zh"] for x in self.named]
        # Whole name first, then runs of 3+ characters no other runner at
        # the meeting shares, longest first.
        self.patterns: list[tuple[str, dict, float]] = []
        for x in self.named:
            n = x["name_zh"]
            self.patterns.append((n, x, EXACT))
            for size in range(len(n) - 1, 2, -1):
                for i in range(len(n) - size + 1):
                    frag = n[i:i + size]
                    if sum(frag in m for m in all_zh) == 1:
                        self.patterns.append((frag, x, FRAGMENT))

    def race(self, race_no: int) -> list[dict]:
        return [x for x in self.runners if x["race_no"] == race_no]

    def mentions(self, text: str) -> list[tuple[int, str, dict, float]]:
        """(position, said, runner, confidence), one per runner, earliest."""
        found: dict[tuple[int, int], tuple[int, str, dict, float]] = {}
        for said, x, conf in self.patterns:
            pos = text.find(said)
            key = (x["race_no"], x["horse_no"])
            if pos >= 0 and (key not in found or conf > found[key][3]):
                found[key] = (pos, said, x, conf)
        return sorted(found.values(), key=lambda m: m[0])


