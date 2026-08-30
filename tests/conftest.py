"""Test-wide setup.

The API refuses to start without `HKRD_PASSWORD` — deliberately, because the
dashboard serves the whole betting ledger and a deploy that forgot its secret
must fail rather than publish it. The suite is a local instance nothing else
can reach, so it takes the explicit opt-out; `test_auth.py` sets a password of
its own where the point is the password.
"""
from __future__ import annotations

import os

os.environ.setdefault("HKRD_ALLOW_NO_AUTH", "1")
