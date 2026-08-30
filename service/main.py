"""
aify-comms — Main FastAPI Application (v2 SQLite)
"""

import asyncio
import hmac
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from service.longpoll import attributable_ms, begin_wait_accounting

from service.config import get_config
from service.api_core.browser_origin import (
    browser_request_is_allowed, is_browser_navigation, url_without_api_key,
)
from service.routers import health, containers as containers_router
from service.routers.api_v2 import router as api_router
from service.db import init_db
from service.ws import ConnectionManager
from service.ntfy import get_relay


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Pinpoint "database is locked" and slow handlers, without crying wolf at long polls.

    Diagnostic (2026-06-29): a lock error only fires after the 5s busy_timeout, so the offending
    request shows up as a ~5s request to a specific endpoint. Logging method+path+duration lets the
    wide-transaction source be fixed precisely instead of guessed. Cheap: one monotonic clock per
    request.

    A CLASS RATHER THAN A `@app.middleware("http")` CLOSURE, for the same reason `APIKeyMiddleware`
    is one: a closure defined inside `create_app` can only be exercised by building the whole
    application, and `create_app()` opens a real database at a config-derived path, mounts the MCP
    SSE server and runs a startup reconcile. A test wanting to check a log line should not have to do
    any of that -- and a test that opens a database whose path comes from configuration is one
    misconfiguration away from opening the operator's.

    A LONG POLL IS NOT A SLOW REQUEST. It holds the connection open on purpose, and every one of them
    tripped the old flat 1000ms threshold: measured over six hours of the live service's logs, 10,587
    of 14,062 SLOW-REQ lines (75.3%) were `/claim` polls returning at their own wait budget, and
    `/api/v1/environments/controls/claim` had a MINIMUM of 20,002ms. The lines that mattered --
    `/api/v1/agents` reaching 5,578ms -- were buried three-to-one in the one log the debug skill sends
    an operator to read.

    NO PATH LIST, because none could be right: the budget is per-REQUEST (`waitMs` in the body, capped
    by `MAX_WAIT_S`), so the same endpoint is an immediate return for one caller and a 20-second hold
    for another. The waiting reports ITSELF through `begin_wait_accounting`, and what remains is work.
    """

    #: Work, in milliseconds, at or above which a request is worth a line. Not the wall clock.
    SLOW_MS = 1000

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        wait_holder = begin_wait_accounting()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 — log + re-raise, behavior unchanged
            dur_ms = int((time.monotonic() - start) * 1000)
            if "database is locked" in str(exc).lower() or "locked" in str(exc).lower():
                logger.error(f"DB-LOCK {request.method} {request.url.path} after {dur_ms}ms: {exc}")
            else:
                logger.error(
                    f"REQ-ERROR {request.method} {request.url.path} after {dur_ms}ms: "
                    f"{type(exc).__name__}: {exc}"
                )
            raise
        dur_ms = int((time.monotonic() - start) * 1000)
        work_ms = attributable_ms(dur_ms, wait_holder)
        if work_ms >= self.SLOW_MS:
            waited = dur_ms - work_ms
            # BOTH NUMBERS WHEN THERE WAS A WAIT. "3200ms" on a request that slept 20s of its 23s
            # tells an operator the wrong thing about where the time went.
            detail = f" (waited {waited}ms of {dur_ms}ms)" if waited else ""
            logger.warning(
                f"SLOW-REQ {request.method} {request.url.path} {work_ms}ms"
                f"{detail} status={response.status_code}"
            )
        if response.status_code >= 500:
            logger.error(
                f"REQ-5XX {request.method} {request.url.path} {dur_ms}ms status={response.status_code}"
            )
        return response


class CrossSiteBrowserMiddleware(BaseHTTPMiddleware):
    """Refuse requests a browser made from a page on another site.

    THE THREAT, as KNOWN_ISSUES has recorded since the 2026-06-28 audit: with CORS `*` and no key, a
    page the operator merely visits can drive every mutating endpoint -- including
    `POST /agents/{id}/console/input`, which types into a live PTY -- and read every response. Binding
    loopback does not help: the browser is already on the machine.

    WHY A HEADER AND NOT A KEY. A key has to be generated, distributed to every client and rotated,
    and it is opt-in for exactly that reason. `Sec-Fetch-Site` is attached by the BROWSER and cannot be
    removed by page script, and no program sends it -- so this protects the default deployment, which
    is the one that needed protecting.

    ABSENT MEANS "not a browser", and that is not a hole: a browser cannot omit this header. Refusing
    on absence would refuse every bridge, every CLI and every `curl` this service exists to serve.
    """

    def __init__(self, app, allowed_origins: list[str] | None = None):
        super().__init__(app)
        # `*` is deliberately NOT an exemption. A wildcard is the absence of a decision about who may
        # drive this service from a browser, and reading it as "everyone" would make the guard a no-op
        # in exactly the default configuration it exists to protect.
        self.allowed_origins = {
            str(origin).strip().rstrip("/").lower()
            for origin in (allowed_origins or [])
            if str(origin).strip() not in ("", "*")
        }

    async def dispatch(self, request: Request, call_next):
        # ONE POLICY, shared with the WebSocket door. This used to key on `Sec-Fetch-Site` alone and
        # never read `Origin`, so a hostile Origin with no Fetch Metadata passed untouched and
        # `same-site` -- the registrable DOMAIN, which a sibling subdomain shares -- passed
        # wholesale. The WebSocket check already compared by host; two guards for one question,
        # disagreeing, is how the weaker one becomes the way in.
        if browser_request_is_allowed(
            method=request.method,
            sec_fetch_site=request.headers.get("sec-fetch-site", ""),
            origin=request.headers.get("origin", ""),
            host=request.headers.get("host", ""),
            allowed_origins=self.allowed_origins,
        ):
            return await call_next(request)
        return Response(
            content='{"error":"Cross-site browser requests are refused. Add the origin to cors_origins in config/service.json if this is your own dashboard."}',
            status_code=403,
            media_type="application/json",
        )


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    #: The cookie a browser gets in exchange for a valid `?api_key=`, so the dashboard is usable at all
    #: once a key is set. Named rather than typed at each site: it is read in one place and written in
    #: another, and a typo between them is a login that silently never sticks.
    COOKIE = "aify_api_key"

    #: Long enough that an operator is not re-pasting a key every day, short enough that a borrowed
    #: browser does not stay authorised for a year.
    COOKIE_MAX_AGE = 30 * 24 * 3600

    async def dispatch(self, request: Request, call_next):
        skip_paths = ["/health", "/ready", "/version", "/docs", "/redoc", "/openapi.json", "/ws", "/favicon", "/api/v1/favicon"]
        if any(request.url.path.startswith(p) for p in skip_paths):
            return await call_next(request)
        # THREE CARRIERS, and the third is why the dashboard works at all. A browser cannot set a
        # header on a document request, so with only `X-API-Key` a protected service serves its own
        # UI a 401 -- measured against the real app, and invisible to the suite, whose base builds an
        # app with no middleware. `?api_key=` is exchanged for a cookie below.
        from_query = request.query_params.get("api_key")
        provided_key = (
            request.headers.get("X-API-Key")
            or from_query
            or request.cookies.get(self.COOKIE)
        )
        # Compare as BYTES (bughunt 2026-07-03): hmac.compare_digest raises TypeError
        # on a str containing non-ASCII code points, which was unhandled → HTTP 500 on
        # every protected endpoint for a garbage key instead of a clean 401. Encoding
        # both sides sidesteps it (compare_digest is still constant-time; it never
        # false-positives, so this is not an auth weakening).
        if not provided_key or not hmac.compare_digest(
            provided_key.encode("utf-8", "ignore"), self.api_key.encode("utf-8", "ignore")
        ):
            return Response(
                content='{"error":"Invalid or missing API key. Use X-API-Key header or ?api_key= param."}',
                status_code=401,
                media_type="application/json",
            )
        response = await call_next(request)
        if from_query:
            # Set only on the request that ARRIVED with a valid key in the URL, never on a request
            # that authenticated by header or by an existing cookie. A program calling with a header
            # has no use for one, and re-setting it on every call would refresh the expiry of a
            # browser session nobody is using.
            #
            # `samesite="lax"` IS Starlette's default -- checked, not assumed, after a mutation that
            # deleted the argument left the header unchanged. It is written out anyway because it is
            # the security property this cookie stands on, not a formatting preference: a cookie is
            # sent automatically, so without Lax every state-changing route becomes reachable from any
            # page the operator visits -- a hole the header-only scheme did not have, opened by the fix
            # for a different problem. A default nobody stated is a default somebody upgrades away.
            # `httponly` is NOT a default (Starlette's is False) and is doing work on its own.
            # A BROWSER NAVIGATION GETS THE KEY OUT OF ITS URL. The documented way to open the
            # dashboard once a key is set is to visit `/?api_key=<value>` -- which works, and then
            # leaves the credential in history, in the address bar, in any bookmark made from that
            # page, and in the `Referer` of every outbound link. None of that is needed: the cookie
            # is already set on this very response.
            #
            # SCOPED to a navigation, and detected POSITIVELY. A program passing `?api_key=` is a
            # supported caller whose contract a 303 would change, and its URL is in nobody's
            # history. `Sec-Fetch-Dest: document` is set by browsers and by no program; inferring
            # "browser" from an absence would sweep in every one of them.
            #
            # AFTER the key check, never before: redirecting first would turn a rejected credential
            # into a 303 that looks like success.
            if is_browser_navigation(request.headers.get("sec-fetch-dest", ""), request.method):
                # RELATIVE, deliberately. `str(request.url)` is absolute and its authority comes
                # from the `Host` header, so reflecting it would put a client-supplied value into a
                # redirect target and would also pin a scheme this service may not be reached on
                # behind a proxy. A path-relative Location keeps the browser exactly where it is.
                cleaned = url_without_api_key(
                    f"{request.url.path}?{request.url.query}" if request.url.query
                    else request.url.path
                )
                redirect = RedirectResponse(url=cleaned or "/", status_code=303)
                redirect.set_cookie(
                    self.COOKIE, self.api_key,
                    max_age=self.COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
                )
                return redirect
            response.set_cookie(
                self.COOKIE, self.api_key,
                max_age=self.COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
            )
        return response


def _setup_logging(config):
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    if config.log_format == "json":
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    else:
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout, force=True)


logger = logging.getLogger(__name__)

# The 287-line reconcile sweep moved to service/reconcilers/sweep.py in v0.5.4 — it
# orchestrates the reconcilers and starts nothing, so it belongs in that layer rather
# than in the process entry point. Imported here because the periodic task below and
# the startup path both call it, and because tests reach it as
# `service.main._run_dispatch_reconcile_once`, which an import keeps resolving.
from service.reconcilers.sweep import _run_dispatch_reconcile_once, reportable




async def _periodic_dispatch_reconcile() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            result = await _run_dispatch_reconcile_once()
            visible = reportable(result)
            if visible:
                logger.info(f"Periodic dispatch reconcile: {visible}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Periodic dispatch reconcile skipped: {e}")


def websocket_origin_is_allowed(origin: str, host: str, allowed_origins) -> bool:
    """May a WebSocket handshake carrying this `Origin` be accepted?

    DELEGATES to `browser_request_is_allowed`, which is the same decision the HTTP door asks. This
    function held the BETTER of the two implementations -- it compared by host, so the second
    dashboard on another port still worked -- while the HTTP guard keyed on `Sec-Fetch-Site` alone.
    Keeping the better copy here and the weaker one there is how a service ends up with a front door
    and a side door that disagree, and an attacker only needs the weaker.

    A handshake is a GET and browsers do not attach Fetch Metadata to it, so the origin comparison
    is the whole test: no Origin means not a browser, and those callers -- bridges, CLIs, tests --
    are what this endpoint exists to serve.
    """
    return browser_request_is_allowed(
        method="GET", sec_fetch_site="", origin=origin, host=host, allowed_origins=allowed_origins,
    )


async def _authorize_websocket(ws: WebSocket, api_key: str) -> bool:
    provided_key = (
        ws.headers.get("X-API-Key")
        or ws.query_params.get("api_key")
    )
    # Bytes comparison — a non-ASCII key would TypeError on the str form (see the
    # middleware note); here it would bubble out of the WS handshake. (bughunt 2026-07-03)
    if provided_key and hmac.compare_digest(
        provided_key.encode("utf-8", "ignore"), (api_key or "").encode("utf-8", "ignore")
    ):
        return True
    await ws.close(code=1008, reason="Invalid or missing API key")
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    _setup_logging(config)
    logger.info(f"Starting {config.name} v{config.version} (SQLite)")

    # Init SQLite database
    db_path = Path(config.data_dir) / "aify.db"
    await init_db(db_path)
    logger.info(f"Database: {db_path}")

    # Bounded startup reconcile: drain delivered dispatch runs that never got
    # a terminal state (reply-linked, or stale and not requiring a reply) so
    # the open-run ledger does not accumulate forever. Conservative — never
    # closes runs still legitimately awaiting a required reply. Failure here
    # must never block startup.
    try:
        result = await _run_dispatch_reconcile_once()
        visible = reportable(result)
        if visible:
            logger.info(f"Startup dispatch reconcile/prune: {visible}")
    except Exception as e:
        logger.error(f"Startup dispatch reconcile/prune skipped: {e}")

    reconcile_task = asyncio.create_task(_periodic_dispatch_reconcile())

    # Plan 2, Task 17 — periodic pi-resident drain & flip. Pi agents that
    # registered as sessionMode=resident are marked with a pending-flip
    # flag; once no open runs are targeting them, the loop migrates them
    # to sessionMode=managed. 5s tick keeps the flip latency tight.
    from service.pi_resident_flip import _periodic_pi_resident_flip_loop
    pi_flip_task = asyncio.create_task(_periodic_pi_resident_flip_loop())

    # WebSocket manager
    app.state.ws_manager = ConnectionManager()

    # Store config on app state
    app.state.config = config

    # Container manager (optional)
    container_manager = None
    json_path = Path(config.config_dir) / "service.json"
    if json_path.exists():
        try:
            with open(json_path) as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {json_path}: {e}")
            config_data = {}

        if config_data.get("containers", {}).get("definitions"):
            from service.containers.manager import ContainerManager, load_container_definitions
            try:
                definitions, defaults = load_container_definitions(config_data)
                container_manager = ContainerManager(definitions, defaults)
                app.state.container_manager = container_manager
                await container_manager.start_background_tasks()
                logger.info(f"Container manager: {len(definitions)} containers defined")
            except Exception as e:
                logger.error(f"Container manager init failed: {e}")

    # Mount MCP server if enabled
    if config.mcp_enabled:
        try:
            import importlib.util
            _sse_path = Path(__file__).resolve().parent.parent / "mcp" / "sse_server.py"
            _spec = importlib.util.spec_from_file_location("sse_server", _sse_path)
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _mod.setup_mcp_server(app)
            logger.info(f"MCP SSE at {config.mcp_path_prefix}/sse")
        except Exception as e:
            # WARNING, not info. A transport that failed to mount is not the same as one nobody
            # configured, and reading it as routine is how this stayed broken unmeasured.
            logger.warning(f"MCP SSE server FAILED to mount at {config.mcp_path_prefix}/sse: {e}")

    # v0.4 C3 — the ntfy drain task. It owns the network and the timeout so no request path ever
    # does. `start()` is a no-op when AIFY_NTFY_URL is unset, which is the default, so an operator
    # who never configures ntfy gets no task and no behaviour change at all.
    ntfy_relay = get_relay()
    ntfy_relay.start()
    if ntfy_relay.enabled:
        # C6 — `.redacted`, never the raw URL. Anyone holding the topic can read every alert sent
        # to it and publish to it, so it is a credential and a startup log is still a log.
        logger.info(f"ntfy mobile alerts enabled -> {ntfy_relay.redacted}")

    try:
        yield
    finally:
        await ntfy_relay.stop()
        reconcile_task.cancel()
        try:
            await reconcile_task
        except asyncio.CancelledError:
            pass
        pi_flip_task.cancel()
        try:
            await pi_flip_task
        except asyncio.CancelledError:
            pass

    # --- SHUTDOWN ---
    if container_manager:
        await container_manager.shutdown()
    logger.info(f"Shutting down {config.name}")




def create_app() -> FastAPI:
    config = get_config()

    app = FastAPI(
        title=config.name,
        version=config.version,
        description=config.description,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # COMPRESSION. Measured against this instance on 2026-08-25: one dashboard poll cycle fetches
    # 1,093,414 bytes across its six largest endpoints, and the service offered no encoding at all --
    # `curl --compressed` returned the identical byte count, because nothing was there to negotiate
    # with. The same bytes gzip to 243,370, a 4.5x cut, which takes 250 MB/hour down to 55 MB/hour
    # per OPEN TAB at the ~15s poll. Per endpoint the ratio runs 2x (message bodies, already prose)
    # to 12x (/sessions, which is mostly repeated keys and ids).
    #
    # 500 bytes as the floor: below that a gzip header costs more than it saves, and the small
    # responses here (/settings at 1,477 and /stats at 2,395) are the ones latency shows up on.
    #
    # This does not touch the larger waste beside it. 935 KB of that cycle was BYTE-IDENTICAL across
    # a six-second gap -- spawn-requests, messages/recent and the inbox -- and the service supports
    # no conditional request, so `If-None-Match` returns a full 200 rather than a 304. Compression
    # makes re-sending unchanged data cheaper; it does not stop it.
    # CHECKED BEFORE ADDING, because a middleware over EVERY response is the kind of change whose
    # damage shows up somewhere nobody was looking:
    #   * the dashboard console is a WEBSOCKET, and this handles only scope 'http', so the visible
    #     TUI is untouched. That requirement is not negotiable and was verified, not assumed.
    #   * the one StreamingResponse (service/containers/proxy.py) strips content-encoding and
    #     httpx has already decoded the body, so nothing is compressed twice; and starlette 1.6.0
    #     asks zlib for a sync flush on each streamed chunk, so a proxied stream is not held back
    #     waiting for a deflate block to fill.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # CORS
    origins = config.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=("*" not in origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key auth
    if config.api_key:
        app.add_middleware(APIKeyMiddleware, api_key=config.api_key)
        logger.info("API key auth enabled")

    # Cross-site browser requests, refused whether or not a key is configured.
    #
    # ADDED AFTER THE KEY MIDDLEWARE ON PURPOSE. Starlette runs middleware in REVERSE order of
    # `add_middleware`, so the last one added is the outermost -- and this has to see a request before
    # the key check does, because the deployment it protects is the one with NO key. It also sits
    # outside CORS, so a cross-site preflight is refused before CORS gets to approve it.
    #
    # ALWAYS ON. Unlike the key and the bind address, this costs an operator nothing: no program sends
    # `Sec-Fetch-Site`, and both dashboards are same-origin or same-site.
    app.add_middleware(CrossSiteBrowserMiddleware, allowed_origins=config.cors_origins)

    app.add_middleware(RequestTimingMiddleware)

    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(containers_router.router)

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # BEFORE THE KEY CHECK, and unconditionally. A WebSocket reaches neither middleware --
        # `BaseHTTPMiddleware` passes non-http scopes straight through -- and WebSocket handshakes are
        # not subject to CORS, so a page on any site could open this stream and read fleet activity
        # with no key configured, which is the default.
        if not websocket_origin_is_allowed(
            ws.headers.get("origin", ""), ws.headers.get("host", ""), config.cors_origins
        ):
            await ws.close(code=1008)
            return
        if config.api_key and not await _authorize_websocket(ws, config.api_key):
            return
        agent_id = ws.query_params.get("agent_id")
        manager = app.state.ws_manager
        await manager.connect(ws, agent_id)
        try:
            while True:
                await ws.receive_text()  # Keep alive, ignore client messages
        except WebSocketDisconnect:
            manager.disconnect(ws)

    # Dashboard Next is the only operator UI. Keep the API root as a compatibility
    # redirect so old bookmarks and tooling converge on it instead of breaking.
    from fastapi.responses import RedirectResponse
    from service.dashboard_redirect import dashboard_url

    @app.get("/", include_in_schema=False)
    async def root_redirect(request: Request):
        return RedirectResponse(url=dashboard_url(request))

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon_svg():
        return FileResponse(Path(__file__).resolve().parent / "favicon.svg", media_type="image/svg+xml")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon_ico():
        return FileResponse(Path(__file__).resolve().parent / "favicon.svg", media_type="image/svg+xml")

    return app


app = create_app()
