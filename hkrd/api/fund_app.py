"""The fund API's own app: a private port, a bearer token, and nothing else.

A separate FastAPI app rather than routes on the dashboard, so that nothing on
the internet-facing port can answer for the fund and nothing here can answer
for the dashboard. `hkrd.listen` serves it on [::]:8081, which Fly's private
network reaches and its public proxy does not (the port is in no `services`
block). The token is the second lock, not the first.

It FAILS CLOSED. Without FUND_API_TOKEN every request is a 503 naming the
missing secret -- the same refusal to serve unauthenticated that `api/auth`
makes for the dashboard. The token is read on each request, so a secret set
by a redeploy is the one in force.
"""
from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response

from hkrd.api.routes import fund

TOKEN_ENV = "FUND_API_TOKEN"

app = FastAPI(title="hkrd fund API", version="1",
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=900)
app.include_router(fund.router)


@app.middleware("http")
async def _bearer(request: Request, call_next) -> Response:
    """Every route, health included, needs `Authorization: Bearer <token>`."""
    expected = os.environ.get(TOKEN_ENV, "")
    if not expected:
        return JSONResponse({"detail": f"{TOKEN_ENV} is not set; the fund API "
                                       "refuses to serve without one"}, 503)
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given.encode(), f"Bearer {expected}".encode()):
        return JSONResponse({"detail": "missing or wrong bearer token"}, 401,
                            headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)
