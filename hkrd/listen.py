"""Production server: the dashboard and, beside it, the fund API. One process.

    python -m hkrd.listen --host 0.0.0.0 --port 8080

The dashboard is served exactly as `uvicorn hkrd.api.app:app` served it. The
fund API (`api/fund_app`) is served on [::]:FUND_API_PORT (8081), the address
Fly's private network reaches, and only when FUND_API_TOKEN is set.

ONE PROCESS, NOT TWO. Both apps read the same SQLite file through the same
query code, and a second Python process would hold its own copy of every
import -- tens of megabytes resident on a machine whose memory is most of the
bill. Two uvicorn servers on one event loop cost one interpreter.

THE DASHBOARD COMES FIRST. A fund listener that cannot bind, or dies, is
logged and the dashboard carries on: the fund app keeps its own copy and
shows its age, and losing race-day pages to a side door would be the worse
trade. The other way round is not true -- when the dashboard stops, so does
the fund API, so the process exits as it always has.

Signals: each uvicorn server installs its handler on start and restores the
previous one, re-raising, on exit. The fund server starts second, so SIGTERM
stops it first and is passed on to the dashboard, which stops and lets the
default action end the process -- what a lone uvicorn does.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket

import uvicorn

__all__ = ["FUND_PORT_ENV", "fund_socket", "build", "serve", "main"]

log = logging.getLogger("hkrd.listen")

FUND_PORT_ENV = "FUND_API_PORT"
FUND_TOKEN_ENV = "FUND_API_TOKEN"     # the same name api/fund_app reads


def fund_socket(port: int, host: str = "::") -> socket.socket | None:
    """A listening socket for the fund API, or None (logged) if it can't bind.

    Bound here rather than by uvicorn, which answers a failed bind with
    sys.exit -- and that would take the dashboard down with it. Dual-stack, so
    the same port answers 127.0.0.1 for a local check.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        # Rebind straight after a restart (Linux). Not on Windows, where the
        # same option lets a second listener take a port already in use.
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind((host, port))
        sock.listen(128)
    except OSError as exc:
        sock.close()
        log.error("fund API NOT SERVED: cannot listen on [%s]:%s (%s); "
                  "the dashboard carries on without it", host, port, exc)
        return None
    sock.setblocking(False)
    return sock


def build(host: str, port: int, fund_port: int | None
          ) -> tuple[uvicorn.Server, uvicorn.Server | None, socket.socket | None]:
    """The dashboard server, and the fund server with its socket when it can run."""
    dash = uvicorn.Server(uvicorn.Config("hkrd.api.app:app", host=host, port=port))
    if fund_port is None:
        return dash, None, None
    if not os.environ.get(FUND_TOKEN_ENV):
        log.warning("fund API off: %s is not set", FUND_TOKEN_ENV)
        return dash, None, None
    sock = fund_socket(fund_port)
    if sock is None:
        return dash, None, None
    fund = uvicorn.Server(uvicorn.Config(
        "hkrd.api.fund_app:app", host="::", port=fund_port, lifespan="off"))
    return dash, fund, sock


def _fund_ended(task: asyncio.Task) -> None:
    """A fund server that stops on its own is said, never swallowed."""
    if task.cancelled():
        log.error("fund API stopped: cancelled")
    elif (exc := task.exception()) is not None:
        log.error("fund API stopped: %r; the dashboard carries on", exc)


async def serve(dash: uvicorn.Server, fund: uvicorn.Server | None = None,
                sock: socket.socket | None = None) -> None:
    """Run until the dashboard stops, then stop the fund server too."""
    dash_task = asyncio.create_task(dash.serve())
    fund_task = None
    if fund is not None and sock is not None:
        fund_task = asyncio.create_task(fund.serve(sockets=[sock]))
        fund_task.add_done_callback(_fund_ended)
    try:
        await dash_task
    finally:
        if fund_task is not None and not fund_task.done():
            fund.should_exit = True
            await asyncio.wait([fund_task])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("HKRD_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HKRD_PORT", "8080")))
    ap.add_argument("--fund-port", type=int,
                    default=int(os.environ.get(FUND_PORT_ENV, "8081")))
    ap.add_argument("--no-fund", action="store_true", help="serve the dashboard only")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:  %(message)s")
    dash, fund, sock = build(a.host, a.port, None if a.no_fund else a.fund_port)
    try:
        asyncio.run(serve(dash, fund, sock))
    except KeyboardInterrupt:
        # Fly stops the machine with SIGINT. By the time this is raised both
        # servers have shut down cleanly: uvicorn re-raises the signal once it
        # has, and asyncio reports that as KeyboardInterrupt. `uvicorn.run`
        # catches it the same way; uncaught, it printed a traceback on every
        # deploy (6 Nov 2026, v75 to v76) that looked like a crash.
        log.info("stopped by SIGINT after a clean shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
