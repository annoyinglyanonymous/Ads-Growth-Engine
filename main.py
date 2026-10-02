"""The dashboard process.

    python -m main                 # 127.0.0.1:8001
    python -m main --port 8100
    python -m main --reload

127.0.0.1 and never localhost: on Windows `localhost` resolves ::1 first, and a
uvicorn bound to 127.0.0.1 is not listening there -- which presents as a refused
connection against an app that is plainly running. growth-engine's CLAUDE.md
carries the same rule.

8001 because growth-engine owns 8000, and both are routinely up at once.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
from config import NotConfigured, settings

ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Said at startup rather than discovered on the first page. An unset
    # ADS_DATABASE_URL_RO is the single most likely misconfiguration here, and
    # it otherwise surfaces as an error on whichever page happens to load first.
    try:
        settings.read_url
        await db.open_read()
        print(f"  reading as ads_reader")
    except NotConfigured as exc:
        print(f"  NOT CONFIGURED: {exc}", file=sys.stderr)
    except Exception as exc:  # pragma: no cover - startup diagnostics
        print(f"  database not reachable yet: {exc}", file=sys.stderr)

    print("  This app reads. It approves nothing and never pulls in-process.")
    print("  Refresh spawns scripts\\sync.py detached, like the scheduler.")
    yield
    await db.close_read()


app = FastAPI(title="Ads Intelligence", lifespan=lifespan, docs_url=None,
              redoc_url=None)

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

import ui  # noqa: E402 -- must come after `app` exists, as in growth-engine

app.include_router(ui.router)


@app.get("/health", include_in_schema=False)
async def health():
    return {"ok": True}


@app.exception_handler(404)
async def not_a_page(request, exc):
    if "text/html" in (request.headers.get("accept") or ""):
        return HTMLResponse(
            "<h1>Not a page</h1><p><a href='/'>Overview</a></p>", status_code=404)
    return JSONResponse({"error": "not found"}, status_code=404)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=settings.ads_host)
    p.add_argument("--port", type=int, default=settings.ads_port)
    p.add_argument("--reload", action="store_true")
    a = p.parse_args()

    import uvicorn
    print(f"\n  Ads Intelligence -> http://{a.host}:{a.port}\n")

    if a.reload:
        uvicorn.run("main:app", host=a.host, port=a.port, reload=True)
        return 0

    # Windows: uvicorn >= 0.36 hands back a ProactorEventLoop, which psycopg
    # cannot use in async mode -- and the pool hides that as a PoolTimeout after
    # 30 seconds, which looks exactly like a network problem. So build the loop
    # here and tell uvicorn not to. Lifted from growth-engine/main.py, where the
    # same half-hour was already spent once.
    if sys.platform == "win32":
        config = uvicorn.Config(app, host=a.host, port=a.port, loop="none")
        server = uvicorn.Server(config)
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            try:
                runner.run(server.serve())
            except KeyboardInterrupt:
                # Ctrl+C is how this process is meant to end, so it should not
                # print a traceback. uvicorn has already caught SIGINT, run its
                # shutdown and closed the pool by the time we get here; what
                # asyncio.Runner re-raises is the tail of an orderly stop, not a
                # fault. Left unhandled it prints a CancelledError chained to a
                # KeyboardInterrupt directly beneath "Application shutdown
                # complete", which reads as a crash during shutdown.
                pass
        return 0

    try:
        uvicorn.run(app, host=a.host, port=a.port)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
