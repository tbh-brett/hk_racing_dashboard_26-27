"""One shared password, in front of everything.

The dashboard holds the owner's complete betting ledger — 1,078 bets, $53,241
staked, every selection and every return — the blackbook, and the bookie
references from their account statements. Hosted without this, all of it is
public to anyone who finds the URL.

WHY A SHARED SECRET AND NOT ACCOUNTS. There is one user. Accounts would add a
users table, a password-reset path and a session store to maintain, and none
of them would make this safer: the thing being protected is one person's
private data, and one person's password is the whole of the access control
that data needs.

WHY IT FAILS CLOSED. `HKRD_PASSWORD` unset does not mean "run without a
password" — it means the deploy forgot the secret, and serving the ledger to
the internet is the wrong response to that. The app refuses to start unless
either the password is set or `HKRD_ALLOW_NO_AUTH=1` says so explicitly, which
is how it runs on a laptop. Silent success and silent failure looking identical
is the fault this whole package exists to remove, and it would be a poor place
to make an exception.

The cookie is a signed timestamp, not a stored session: there is nothing to
expire server-side, nothing to replicate to the second machine, and nothing to
lose when the process restarts. HMAC-SHA256 over the expiry with the password
as the key, compared in constant time.

A SECOND PASSWORD, FOR THE LEDGER ALONE. The site password is now shared with
other people who read the race pages, so it no longer protects the bets. When
`HKRD_BETS_PASSWORD` is set, the Bets page, every `/api/bets` route and the
statement import also need a second cookie, signed with that password. It sits
BEHIND the site password, not instead of it: someone holding only the bets
password still cannot get past the front door. Unset, the Bets page is behind
the site password alone, which is how it ran before there were other readers.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import posixpath
import re
import time
from collections import deque
from typing import Any
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

__all__ = ["AuthError", "configure", "is_open", "bets_locked", "install",
           "COOKIE", "BETS_COOKIE", "SESSION_HOURS"]

COOKIE = "hkrd_session"
BETS_COOKIE = "hkrd_bets"
SESSION_HOURS = 24 * 14        # a fortnight: long enough not to nag, short
                               # enough that a stolen laptop is not forever

# Paths served without a session. Deliberately short: the health check, because
# the platform calls it before a session can exist, and the login pair itself.
# Logout is open because it only ever DELETES a cookie — gating it means a
# stale session cannot be cleared by the person holding it.
#
# The login page's own stylesheets are here because a redirect is not a
# stylesheet: gating them returned the login HTML with a text/html type, the
# browser refused to apply it, and the sign-in page rendered as unstyled
# system-font text. They are named one by one rather than opening /assets/,
# so a new file under there is private until someone says otherwise.
# `test_the_login_page_can_load_everything_it_asks_for` reads the list out of
# login.html, so adding a fourth stylesheet there fails the suite rather than
# the page.
_OPEN_PATHS = frozenset({"/api/health", "/login", "/api/login", "/api/logout",
                         "/favicon.svg",
                         "/assets/tokens.css", "/assets/pages.css",
                         "/assets/login.css"})

# A password is not a rate limiter. Five attempts a minute from one address is
# far more than a person types and far less than a script needs.
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 60
_attempts: dict[str, deque[float]] = {}

# What the bets password guards: the page, every route under /api/bets, and the
# statement import, which writes into the same ledger. Endpoints elsewhere that
# only JOIN the ledger (the blackbook's backed-vs-missed) are not on it.
_BETS_PATHS = frozenset({"/pages/bets.html", "/api/jobs/import-statement"})


class AuthError(RuntimeError):
    """The app is configured in a way that would expose the ledger."""


_password: str | None = None
_open = False
_bets_password: str | None = None


def configure(password: str | None = None, *,
              allow_no_auth: bool | None = None,
              bets_password: str | None = None) -> bool:
    """Read the configuration and refuse anything that fails open.

    Returns True when a password is in force. Raises when neither a password
    nor an explicit opt-out is present, which is the case where a deploy has
    forgotten its secret.
    """
    global _password, _open, _bets_password
    if password is None:
        password = os.environ.get("HKRD_PASSWORD") or None
    if allow_no_auth is None:
        allow_no_auth = os.environ.get("HKRD_ALLOW_NO_AUTH") == "1"
    if bets_password is None:
        bets_password = os.environ.get("HKRD_BETS_PASSWORD") or None

    if bets_password:
        if len(bets_password) < 12:
            raise AuthError(
                "HKRD_BETS_PASSWORD is shorter than 12 characters. It is the "
                "only thing between the other readers and the betting ledger.")
        if password and hmac.compare_digest(bets_password, password):
            # Every reader of the site has that one, so it would lock nothing.
            raise AuthError(
                "HKRD_BETS_PASSWORD is the same as HKRD_PASSWORD, which every "
                "reader of the site has. It must be a different secret.")
    _bets_password = bets_password

    if password:
        if len(password) < 12:
            # Not a style preference. This is the only secret in front of the
            # whole ledger, and it is exposed to the open internet.
            raise AuthError(
                "HKRD_PASSWORD is shorter than 12 characters. It is the only "
                "thing between the internet and the betting ledger.")
        _password, _open = password, False
        return True

    if allow_no_auth:
        _password, _open = None, True
        return False

    raise AuthError(
        "HKRD_PASSWORD is not set. The dashboard serves the full betting "
        "ledger and blackbook, so it will not start without one. Set the "
        "password, or set HKRD_ALLOW_NO_AUTH=1 if this really is a local "
        "instance nothing else can reach.")


def is_open() -> bool:
    """True when running with no password, by explicit opt-out."""
    return _open


def bets_locked() -> bool:
    """True when the Bets page needs its own password."""
    return _bets_password is not None


def _is_bets_path(path: str) -> bool:
    # Normalised first: `//pages/bets.html`, `/pages/./bets.html` and, on a
    # case-blind disk, `/pages/BETS.html` all reach the same file.
    path = posixpath.normpath(re.sub(r"/+", "/", path)).lower()
    return (path in _BETS_PATHS or path == "/api/bets"
            or path.startswith("/api/bets/"))


def _sign(expires_at: int, secret: str | None = None) -> str:
    key = ((_password if secret is None else secret) or "").encode()
    mac = hmac.new(key, str(expires_at).encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{mac}"


def _valid(token: str | None, secret: str | None = None) -> bool:
    if not token or "." not in token:
        return False
    stamp, _, mac = token.partition(".")
    if not stamp.isdigit():
        return False
    expires_at = int(stamp)
    if expires_at < time.time():
        return False
    # Constant time: a byte-by-byte comparison leaks how much of the signature
    # was right, which is enough to forge one given enough attempts.
    return hmac.compare_digest(_sign(expires_at, secret), token)


def _urlencoded(body: bytes) -> dict[str, str]:
    """Parse the login form without pulling in `python-multipart`.

    Starlette's `request.form()` requires that library even for an ordinary
    urlencoded form, and one password field is not worth a dependency —
    especially one that would then sit in the image for every deploy.
    """
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if v}


def _throttled(client: str) -> bool:
    now = time.monotonic()
    seen = _attempts.setdefault(client, deque())
    while seen and now - seen[0] > _WINDOW_SECONDS:
        seen.popleft()
    if len(seen) >= _MAX_ATTEMPTS:
        return True
    seen.append(now)
    return False


def install(app) -> None:
    """Put the password in front of every route, and add the login pair."""

    @app.middleware("http")
    async def _require_session(request: Request, call_next):
        path = request.url.path
        if path in _OPEN_PATHS:
            return await call_next(request)
        if not _open and not _valid(request.cookies.get(COOKIE)):
            return _refuse(path, "not signed in", "")
        # The ledger's own lock, checked only once the front door is open.
        if (_bets_password and _is_bets_path(path)
                and not _valid(request.cookies.get(BETS_COOKIE),
                               _bets_password)):
            return _refuse(path, "the bets are locked", "scope=bets&")
        return await call_next(request)

    def _refuse(path: str, detail: str, scope: str) -> Any:
        # An API call gets a status it can act on; a browser gets the form.
        if path.startswith("/api/"):
            return JSONResponse({"detail": detail}, status_code=401)
        # Carry where the browser was headed, so signing in lands there rather
        # than dumping everyone on the Race Day page.
        return RedirectResponse(
            f"/login?{scope}next={quote(path, safe='/')}", status_code=303)

    @app.get("/login", include_in_schema=False)
    def login_form() -> Any:
        # Served as a FILE. api/ serialises data; the markup lives in web/
        # with every other page, and the form reads ?bad= from the URL rather
        # than being templated here.
        return FileResponse(_LOGIN_PAGE, media_type="text/html")

    @app.post("/api/login", include_in_schema=False)
    async def login(request: Request) -> Any:
        client = request.client.host if request.client else "unknown"
        if _throttled(client):
            return RedirectResponse("/login?bad=1", status_code=303)
        form = _urlencoded(await request.body())
        supplied = form.get("password", "")
        if not _password or not hmac.compare_digest(supplied, _password):
            return RedirectResponse("/login?bad=1", status_code=303)

        return _signed_in(form, COOKIE, _password)

    # Reached only with a site session already — the middleware sees to that,
    # so the bets password on its own never opens anything.
    @app.post("/api/login/bets", include_in_schema=False)
    async def login_bets(request: Request) -> Any:
        client = request.client.host if request.client else "unknown"
        if _throttled(client):
            return RedirectResponse("/login?scope=bets&bad=1", status_code=303)
        form = _urlencoded(await request.body())
        supplied = form.get("password", "")
        if not _bets_password or not hmac.compare_digest(supplied,
                                                         _bets_password):
            return RedirectResponse("/login?scope=bets&bad=1", status_code=303)
        return _signed_in(form, BETS_COOKIE, _bets_password)

    @app.post("/api/logout", include_in_schema=False)
    def logout() -> Any:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        response.delete_cookie(BETS_COOKIE)
        return response


def _signed_in(form: dict[str, str], cookie: str, secret: str | None) -> Any:
    """Set a signed cookie and send the browser on to where it was headed."""
    expires_at = int(time.time() + SESSION_HOURS * 3600)
    # Only ever a path on this site. An absolute URL here would make the
    # login form an open redirect for anyone who could get the owner to
    # click it — the page guards this too, and the server does not trust
    # the page to have done so.
    target = form.get("next") or "/"
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        cookie, _sign(expires_at, secret),
        max_age=SESSION_HOURS * 3600,
        httponly=True,      # not readable from JavaScript
        samesite="lax",
        # Fly terminates TLS and forwards over HTTP, so this is set from
        # the deployment rather than inferred from the request scheme.
        secure=os.environ.get("HKRD_INSECURE_COOKIE") != "1",
    )
    return response


_LOGIN_PAGE = (Path(__file__).resolve().parent.parent.parent
               / "web" / "pages" / "login.html")
