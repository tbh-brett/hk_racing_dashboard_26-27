"""One shared password, in front of everything.

The dashboard holds the owner's complete betting ledger — 1,078 bets, $53,241
staked — the blackbook, and the bookie references from their account
statements. Hosted without this, all of it is public to anyone with the URL.
"""
from __future__ import annotations

import re
import time

import pytest

from hkrd.api import auth


@pytest.fixture(autouse=True)
def _restore():
    """Each test configures auth for itself and puts it back."""
    saved = (auth._password, auth._open)
    auth._attempts.clear()
    yield
    auth._password, auth._open = saved


# ── configuration fails closed ───────────────────────────────────────────────

def test_no_password_and_no_opt_out_refuses_to_start():
    """Unset does not mean "run without a password" — it means the deploy
    forgot the secret, and publishing the ledger is the wrong response."""
    with pytest.raises(auth.AuthError, match="will not start"):
        auth.configure(password=None, allow_no_auth=False)


def test_running_open_requires_saying_so():
    assert auth.configure(password=None, allow_no_auth=True) is False
    assert auth.is_open() is True


def test_a_short_password_is_refused():
    """It is the only thing between the internet and the betting ledger, so
    this is not a style preference."""
    with pytest.raises(auth.AuthError, match="shorter than 12"):
        auth.configure(password="hunter2", allow_no_auth=False)


def test_a_password_turns_the_gate_on():
    assert auth.configure(password="a-long-enough-secret") is True
    assert auth.is_open() is False


# ── the cookie ───────────────────────────────────────────────────────────────

def test_a_signed_cookie_is_accepted_and_a_forged_one_is_not():
    auth.configure(password="a-long-enough-secret")
    good = auth._sign(int(time.time() + 3600))
    assert auth._valid(good)
    stamp, _, mac = good.partition(".")
    assert not auth._valid(f"{stamp}.{'0' * len(mac)}")
    assert not auth._valid(f"{int(time.time() + 999999)}.{mac}")


def test_an_expired_cookie_is_not_accepted():
    auth.configure(password="a-long-enough-secret")
    assert not auth._valid(auth._sign(int(time.time() - 1)))


def test_a_cookie_signed_with_another_password_is_not_accepted():
    """Changing the password signs everyone out, which is what changing a
    password is for."""
    auth.configure(password="the-first-secret-here")
    token = auth._sign(int(time.time() + 3600))
    auth.configure(password="a-different-secret!!")
    assert not auth._valid(token)


@pytest.mark.parametrize("token", [None, "", "nonsense", ".", "abc.def",
                                   "12345", "notanumber.aaaa"])
def test_a_malformed_cookie_is_rejected_rather_than_raising(token):
    auth.configure(password="a-long-enough-secret")
    assert auth._valid(token) is False


# ── the login form ───────────────────────────────────────────────────────────

def test_the_form_is_parsed_without_a_multipart_dependency():
    """Starlette's request.form() needs python-multipart even for an ordinary
    urlencoded form, and one password field is not worth a dependency that
    would then sit in the image for every deploy."""
    got = auth._urlencoded(b"password=a+secret%21&next=%2Fpages%2Fbets.html")
    assert got == {"password": "a secret!", "next": "/pages/bets.html"}


def test_repeated_attempts_from_one_address_are_throttled():
    """A password is not a rate limiter. Five a minute is far more than a
    person types and far less than a script needs."""
    auth.configure(password="a-long-enough-secret")
    assert not any(auth._throttled("1.2.3.4") for _ in range(auth._MAX_ATTEMPTS))
    assert auth._throttled("1.2.3.4")
    # One address being throttled does not lock anyone else out.
    assert not auth._throttled("5.6.7.8")


# ── the gate, end to end ─────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HKRD_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("HKRD_PASSWORD", "a-long-enough-secret")
    monkeypatch.delenv("HKRD_ALLOW_NO_AUTH", raising=False)
    monkeypatch.setenv("HKRD_INSECURE_COOKIE", "1")

    import warnings
    warnings.filterwarnings("ignore")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/bets")
    def bets():
        return {"bets": ["private"]}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/pages/bets.html")
    def page():
        return {"page": True}

    auth.configure()
    auth.install(app)
    return TestClient(app, follow_redirects=False)


def test_an_api_call_without_a_session_is_401_not_the_data(client):
    r = client.get("/api/bets")
    assert r.status_code == 401
    assert "private" not in r.text


def test_a_page_without_a_session_is_sent_to_the_login_form(client):
    """A browser gets the form; an API call gets a status it can act on."""
    r = client.get("/pages/bets.html")
    assert r.status_code == 303
    # The destination rides along, so signing in lands where the click was
    # aimed rather than dumping every visit on the Race Day page.
    assert r.headers["location"] == "/login?next=/pages/bets.html"


def test_signing_in_returns_to_where_the_browser_was_headed(client):
    r = client.post("/api/login",
                    data={"password": "a-long-enough-secret",
                          "next": "/pages/bets.html"},
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303
    assert r.headers["location"] == "/pages/bets.html"


@pytest.mark.parametrize("hostile", [
    "https://evil.example/steal",   # absolute, another origin
    "//evil.example/steal",         # protocol-relative, the same thing
    "http://evil.example",
])
def test_the_login_form_will_not_redirect_off_this_site(client, hostile):
    """Otherwise the login page is an open redirect for anyone who can get
    the owner to click a link, and the owner arrives with a fresh session."""
    r = client.post("/api/login",
                    data={"password": "a-long-enough-secret", "next": hostile},
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_the_health_check_is_open_because_the_platform_calls_it_first(client):
    """Fly asks before a session can exist, and it reveals nothing."""
    assert client.get("/api/health").status_code == 200


def test_the_right_password_opens_the_gate(client):
    r = client.post("/api/login", data={"password": "a-long-enough-secret"},
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303
    cookie = r.cookies.get(auth.COOKIE)
    assert cookie
    body = client.get("/api/bets", cookies={auth.COOKIE: cookie})
    assert body.status_code == 200 and body.json()["bets"] == ["private"]


def test_the_wrong_password_does_not(client):
    r = client.post("/api/login", data={"password": "not-the-secret-at-all"},
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303
    assert r.headers["location"] == "/login?bad=1"
    assert not r.cookies.get(auth.COOKIE)


def test_the_cookie_is_not_readable_from_javascript(client):
    r = client.post("/api/login", data={"password": "a-long-enough-secret"},
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert "httponly" in r.headers["set-cookie"].lower()


def test_logging_out_clears_the_cookie(client):
    r = client.post("/api/logout")
    assert r.status_code == 303
    assert 'hkrd_session=""' in r.headers["set-cookie"] or \
           "hkrd_session=;" in r.headers["set-cookie"]


# ── the login page against the real app ──────────────────────────────────────
#
# The stub app above has no static mount, so it cannot see the fault these
# catch: the sign-in page loading but arriving unstyled, because every
# stylesheet it links was redirected back to the sign-in page.

@pytest.fixture()
def real_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HKRD_DB", str(tmp_path / "real.db"))
    monkeypatch.setenv("HKRD_PASSWORD", "a-long-enough-secret")
    monkeypatch.delenv("HKRD_ALLOW_NO_AUTH", raising=False)

    import warnings
    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient
    from hkrd.api.app import app

    auth.configure()          # app.py configured at import, under the opt-out
    return TestClient(app, follow_redirects=False)


def test_the_login_page_can_load_everything_it_asks_for(real_client):
    """Every stylesheet the page links must be reachable without a session.

    The list is read out of login.html rather than repeated here, so adding a
    fourth one without opening it fails this test instead of the page.
    """
    page = real_client.get("/login")
    assert page.status_code == 200

    hrefs = re.findall(r'<link[^>]+href="([^"]+\.css)"', page.text)
    assert hrefs, "the login page linked no stylesheets — check the regex"

    for href in hrefs:
        if href.startswith("http"):
            continue                      # the font, not ours to serve
        url = "/" + href.lstrip("./").removeprefix("/")
        r = real_client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}, page renders bare"
        assert "css" in r.headers["content-type"], \
            f"{url} came back as {r.headers['content-type']}, not a stylesheet"


def test_a_real_page_is_still_shut(real_client):
    """The open list is the login page's needs, not a hole in the gate."""
    r = real_client.get("/pages/bets.html")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
    assert real_client.get("/assets/bets.js").status_code == 303
