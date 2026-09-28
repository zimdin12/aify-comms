"""Standalone replacement dashboard shell served on port 8801."""

import os
import time
from pathlib import Path

import anyio
from fastapi import FastAPI, Form, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from service.config import get_config
from service.dashboard_access import COOKIE, COOKIE_MAX_AGE, is_authorized, key_matches, login_page, session_token

APP_DIR = Path(__file__).resolve().parent / "new_dashboard"
#: The shell `/` serves behind the login, with the operator key injected. Never an asset.
PAGE = APP_DIR / "index.html"

app = FastAPI(
    title="AIFY Comms Dashboard Next",
    # Same release version as the service — one source (repo-root VERSION, baked into the
    # build stamp). This was independently hardcoded "0.1.0" and never moved.
    version=get_config().version,
    description="Replacement dashboard shell for aify-comms.",
    docs_url=None,
    redoc_url=None,
)

# COMPRESSION, and this app is where most of the page's bytes actually are.
#
# The service on :8800 got GZipMiddleware first, and that covered the polling API and none of this.
# Chrome's own trace of a cold load said so plainly -- Document request latency, 'Compression was
# applied: FAILED' -- and a direct check agreed: /assets/app.js returned 54,605 bytes whether or not
# the client offered gzip.
#
# Measured over the 73 files this app serves, 2026-08-25: 753,268 bytes raw against 258,748 gzipped,
# so 482 KB per cold load, 2.9x. The 60-plus ES modules are the bulk of it -- the SPA loads them by
# relative path with no bundling, which is a deliberate trade this does not change.
#
# NOT A LATENCY WIN ON LOCALHOST, and the trace is explicit: estimated savings FCP 0 ms, LCP 0 ms,
# against a measured LCP of 131 ms. There is no round-trip time here to give back. It is a bandwidth
# win, and it only becomes a latency win for a browser that is not on this machine.
#
# Ordered BEFORE the revalidate_static middleware below so the ETag that middleware relies on is
# computed by StaticFiles over the uncompressed file, exactly as it is today; gzip then negotiates
# on the way out and a 304 still short-circuits both.
app.add_middleware(GZipMiddleware, minimum_size=500)

class AssetsOnly(StaticFiles):
    """Serve the dashboard's assets, and not the test tree that shares their directory.

    The modules the browser loads live beside their own tests and one very large fixture, and the
    mount published all of it. Measured 2026-08-25 against the running service: 88 `*.test.mjs`
    files (988 KB) and a 273 KB fixture holding a whole historical copy of app.js (retired in v0.7)
    were reachable at /assets/ and returned 200. That is 1,262 KB of test source on a
    service compose starts with `--host 0.0.0.0`, so it is not localhost-only.

    No page requests any of it: a cold load traced 126 requests and not one was a test file. So this
    removes surface rather than changing behaviour.

    A DENY RULE, derived from the two shapes rather than a list of the 89 names, because a list
    would go stale the moment a test is added -- silently, and in the direction that publishes more.
    """

    #: Suffixes and directories that are never part of the shipped dashboard.
    REFUSED_SUFFIXES = (".test.mjs", ".test.js")
    REFUSED_DIRECTORIES = ("fixtures",)
    #: The shell itself, which only `/` serves, behind the login. As an asset it skipped the login
    #: (review of 0.7.6, L1); it carried no operator key, but the login is the page's only door.
    REFUSED_PAGES = (PAGE.name,)

    @classmethod
    def is_asset(cls, path: str) -> bool:
        """Pure, so the rule can be tested without a server. `path` is the URL path under the mount.

        NAMES ARE FOLDED the way a case-insensitive filesystem folds them: case, and the trailing dots
        and spaces Windows drops. Compared as typed, `/assets/INDEX.HTML` reached the shell on Windows
        (review of 0.7.6, L1 follow-up). Folding cannot catch every alias (an 8.3 short name), so
        `get_response` also refuses by file identity."""
        parts = [_folded(segment) for segment in str(path or "").replace(chr(92), "/").split("/") if segment]
        if not parts:
            return False
        if any(segment in cls.REFUSED_DIRECTORIES for segment in parts):
            return False
        if len(parts) == 1 and parts[0] in cls.REFUSED_PAGES:
            return False
        return not parts[-1].endswith(cls.REFUSED_SUFFIXES)

    def _refused_files(self) -> frozenset:
        """The identity of every file the name rule refuses, read once from the directory's TRUE names."""
        cached = getattr(self, "_refused_cache", None)
        if cached is None:
            refused = set()
            for root, _dirs, files in os.walk(self.directory):
                for name in files:
                    full = os.path.join(root, name)
                    if self.is_asset(os.path.relpath(full, self.directory)):
                        continue
                    try:
                        refused.add(_file_identity(os.stat(full), full))
                    except OSError:
                        continue
            cached = self._refused_cache = frozenset(refused)
        return cached

    async def get_response(self, path, scope):
        if not self.is_asset(path):
            # 404 rather than 403: whether the file exists is itself the thing not being published.
            raise StarletteHTTPException(status_code=404)
        # ANY SPELLING OF A REFUSED FILE IS THAT FILE: whatever alias the filesystem resolved, a request
        # that lands on a refused file is refused.
        try:
            full_path, stat_result = await anyio.to_thread.run_sync(self.lookup_path, path)
        except (OSError, ValueError):
            full_path, stat_result = "", None
        if stat_result is not None and _file_identity(stat_result, full_path) in self._refused_files():
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


def _folded(name: str) -> str:
    """A file name as a case-insensitive filesystem compares it: case folded, trailing dots and spaces gone."""
    return name.casefold().rstrip(". ")


def _file_identity(stat_result, full_path: str):
    """What makes two paths the same file: device and inode, or, where the filesystem reports no inode,
    the normalised real path."""
    if getattr(stat_result, "st_ino", 0):
        return ("inode", stat_result.st_dev, stat_result.st_ino)
    return ("path", os.path.normcase(os.path.realpath(full_path)))


app.mount("/assets", AssetsOnly(directory=APP_DIR), name="new-dashboard-assets")


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Force browsers to revalidate HTML/JS/CSS on each load (304 when unchanged via ETag).

    The SPA loads ES modules by relative path with no version query. Without revalidation a
    browser can hold a stale module after a redeploy and pair a fresh app.js with an old util.js,
    which throws "module does not provide export X" and white-screens until a manual hard-refresh.
    `no-cache` (revalidate, not `no-store`) keeps the cache but guarantees freshness after deploy;
    on a LAN the 304 round-trip is negligible.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".mjs", ".css", ".html")):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "healthy"}


# THE OPERATOR KEY REACHES THE BROWSER FROM HERE, and only from here, when `.env` sets one.
#
# With `OPERATOR_KEY` set, an actor naming itself "operator" proves nothing -- the service requires
# `X-Aify-Operator-Key` before it will let a caller act on another agent's rows (operator_authz.py). This
# dashboard is a legitimate operator surface, so it is given the key server-side; it is never written
# into a file that git tracks and never logged. Unset (the default since v0.7.5), there is no operator
# gate and nothing is injected.
#
# WHY INJECTION AND NOT A CONFIG ENDPOINT: an endpoint that hands out the key would hand it to anything
# that asks, which is the hole being closed. Injecting it into the served HTML at least ties possession
# to fetching this page.
#
# The honest limit, so nobody reads more into it: anything that can GET this page, or read `.env` on the
# host, can obtain the key. This raises the bar from "type an English word" to "hold a secret"; it is not
# a boundary against an agent with filesystem access. That boundary is authenticating the service itself
# (`API_KEY` is unset on this deployment) and is an operator decision, recorded in docs/V0.6_PLAN.md.
def _index_html() -> str:
    html = PAGE.read_text(encoding="utf-8")
    # The same key the service uses, from `.env` (both containers read it).
    key = str(get_config().operator_key or "")
    if not key:
        return html  # no operator gate: the dashboard marks itself with X-Aify-Operator instead
    # JSON-encoded so a key containing a quote or backslash cannot break out of the script literal.
    import json as _json
    seed = f"<script>window.__AIFY_OPERATOR_KEY__ = {_json.dumps(key)};</script>"
    marker = "</head>"
    if marker in html:
        return html.replace(marker, f"  {seed}{chr(10)}{marker}", 1)
    return seed + html


def _now_seconds() -> int:
    return int(time.time())


def _with_session(response, request: Request, api_key: str):
    """Sign the browser in: a cookie holding its issue time, signed with the API key (dashboard_access.py)."""
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    response.set_cookie(COOKIE, session_token(api_key, _now_seconds()), max_age=COOKIE_MAX_AGE, httponly=True,
                        samesite="strict", secure=https, path="/")
    return response


# THE PAGE THAT CARRIES THE OPERATOR KEY is served only to a browser that has shown the API key
# (v0.7.4; service/dashboard_access.py says why). Everything else gets the login form.
@app.get("/", include_in_schema=False)
async def index(request: Request):
    api_key = str(get_config().api_key or "")
    query_key = request.query_params.get("api_key", "")
    if not is_authorized(api_key, now=_now_seconds(), cookie=request.cookies.get(COOKIE, ""), query_key=query_key):
        return HTMLResponse(login_page(PAGE.read_text(encoding="utf-8")), status_code=401)
    response = HTMLResponse(_index_html())
    return _with_session(response, request, api_key) if key_matches(api_key, query_key) else response


@app.post("/login", include_in_schema=False)
async def login(request: Request, key: str = Form("")):
    api_key = str(get_config().api_key or "")
    if api_key and not key_matches(api_key, key):
        return HTMLResponse(login_page(PAGE.read_text(encoding="utf-8"), refused=True), status_code=401)
    response = RedirectResponse(url="/", status_code=303)
    return _with_session(response, request, api_key) if api_key else response


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return RedirectResponse(url="/")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse(Path(__file__).resolve().parent / "favicon.svg", media_type="image/svg+xml")
