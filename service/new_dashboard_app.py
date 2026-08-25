"""Standalone replacement dashboard shell served on port 8801."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from service.config import get_config

APP_DIR = Path(__file__).resolve().parent / "new_dashboard"

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
    files (988 KB) and `fixtures/app.before-settings-fields.js` (273 KB, a whole historical copy of
    app.js) were reachable at /assets/ and returned 200. That is 1,262 KB of test source on a
    service compose starts with `--host 0.0.0.0`, so it is not localhost-only.

    No page requests any of it: a cold load traced 126 requests and not one was a test file. So this
    removes surface rather than changing behaviour.

    A DENY RULE, derived from the two shapes rather than a list of the 89 names, because a list
    would go stale the moment a test is added -- silently, and in the direction that publishes more.
    """

    #: Suffixes and directories that are never part of the shipped dashboard.
    REFUSED_SUFFIXES = (".test.mjs", ".test.js")
    REFUSED_DIRECTORIES = ("fixtures",)

    @classmethod
    def is_asset(cls, path: str) -> bool:
        """Pure, so the rule can be tested without a server. `path` is the URL path under the mount."""
        parts = [segment for segment in str(path or "").replace(chr(92), "/").split("/") if segment]
        if not parts:
            return False
        if any(segment in cls.REFUSED_DIRECTORIES for segment in parts):
            return False
        return not parts[-1].endswith(cls.REFUSED_SUFFIXES)

    async def get_response(self, path, scope):
        if not self.is_asset(path):
            # 404 rather than 403: whether the file exists is itself the thing not being published.
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


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


# THE OPERATOR KEY REACHES THE BROWSER FROM HERE, and only from here.
#
# Since R5-H1 (2026-08-18) an actor naming itself "operator" proves nothing — the service requires
# `X-Aify-Operator-Key` before it will let a caller act on another agent's rows. This dashboard is a
# legitimate operator surface, so it is given the key server-side; it is never written into a file that
# git tracks and never logged.
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
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    key = str(getattr(get_config(), "operator_key", "") or "")
    if not key:
        return html  # no key configured: the dashboard simply cannot claim operator privilege
    # JSON-encoded so a key containing a quote or backslash cannot break out of the script literal.
    import json as _json
    seed = f"<script>window.__AIFY_OPERATOR_KEY__ = {_json.dumps(key)};</script>"
    marker = "</head>"
    if marker in html:
        return html.replace(marker, f"  {seed}{chr(10)}{marker}", 1)
    return seed + html


@app.get("/", include_in_schema=False)
async def index():
    return HTMLResponse(_index_html())


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return RedirectResponse(url="/")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse(Path(__file__).resolve().parent / "favicon.svg", media_type="image/svg+xml")
