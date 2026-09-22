"""Talking to the dashboard from this PC — shared by extract_tips and push_tips.

The dashboard sits behind one shared password. It is read from HKRD_PASSWORD
in the environment, or from a line `HKRD_PASSWORD=...` in the repo's `.env`
(which git ignores), and from nowhere else: never a command-line flag, where it
would sit in shell history, and never printed.

HKRD_BASE says which dashboard; it defaults to the deployed one. A local
instance started with HKRD_ALLOW_NO_AUTH=1 needs no password, and none is
sent to it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "https://hkrd.fly.dev"


class DashboardError(RuntimeError):
    """The dashboard could not be reached, or would not do what was asked."""


def _from_dotenv(key: str) -> str | None:
    env = REPO / ".env"
    if not env.is_file():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name.strip() == key and not name.startswith("#"):
            return value.strip().strip('"').strip("'") or None
    return None


def setting(key: str) -> str | None:
    return os.environ.get(key) or _from_dotenv(key)


def base_url(explicit: str | None = None) -> str:
    return (explicit or setting("HKRD_BASE") or DEFAULT_BASE).rstrip("/")


def connect(base: str) -> requests.Session:
    """A session signed in to `base`, if a password is configured."""
    s = requests.Session()
    password = setting("HKRD_PASSWORD")
    if not password:
        return s
    try:
        r = s.post(f"{base}/api/login",
                   data={"password": password, "next": "/api/health"},
                   allow_redirects=False, timeout=30)
    except requests.RequestException as exc:
        raise DashboardError(f"could not reach {base} — {exc}") from exc
    if r.status_code != 303 or "bad=1" in r.headers.get("location", ""):
        raise DashboardError(
            f"{base} refused the password in HKRD_PASSWORD (or has had five "
            f"wrong attempts from this address in the last minute)")
    return s


def _answer(r: requests.Response, what: str) -> Any:
    if r.status_code == 401:
        raise DashboardError(
            f"{what}: not signed in — put HKRD_PASSWORD=... in the repo's "
            f".env file, or set it in the environment")
    try:
        body = r.json()
    except ValueError:
        body = {"detail": r.text[:300]}
    if r.status_code >= 400:
        raise DashboardError(f"{what}: {r.status_code} — "
                             f"{body.get('detail', body)}")
    return body


def get(s: requests.Session, base: str, path: str) -> Any:
    try:
        r = s.get(f"{base}{path}", timeout=60)
    except requests.RequestException as exc:
        raise DashboardError(f"could not reach {base} — {exc}") from exc
    if r.status_code == 404:
        return None
    return _answer(r, f"GET {path}")


def post(s: requests.Session, base: str, path: str, body: Any) -> Any:
    try:
        r = s.post(f"{base}{path}", json=body, timeout=120)
    except requests.RequestException as exc:
        raise DashboardError(f"could not reach {base} — {exc}") from exc
    return _answer(r, f"POST {path}")
