"""Route modules, one per page's domain.

`app.py` had grown past the repo's 600-line cap, which is the point at which a
file stops being read and starts being searched. The split is by domain rather
than by HTTP verb, so everything one page needs is in one file and adding a
page adds a file instead of a section.
"""
from hkrd.api.routes import bets, blackbook, lookup, results, trials

__all__ = ["bets", "blackbook", "lookup", "results", "trials"]
