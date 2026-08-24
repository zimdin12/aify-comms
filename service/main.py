"""
aify-comms — Main FastAPI Application (v2 SQLite)
"""

import asyncio
import hmac
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from service.config import get_config
from service.routers import health, containers as containers_router
from service.routers.api_v2 import router as api_router
from service.db import init_db
from service.ws import ConnectionManager
from service.ntfy import get_relay


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        skip_paths = ["/health", "/ready", "/version", "/docs", "/redoc", "/openapi.json", "/ws", "/favicon", "/api/v1/favicon"]
        if any(request.url.path.startswith(p) for p in skip_paths):
            return await call_next(request)
        provided_key = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
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
        return await call_next(request)


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
from service.reconcilers.sweep import _run_dispatch_reconcile_once




async def _periodic_dispatch_reconcile() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            result = await _run_dispatch_reconcile_once()
            visible = {key: value for key, value in result.items() if value}
            if visible:
                logger.info(f"Periodic dispatch reconcile: {visible}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Periodic dispatch reconcile skipped: {e}")


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
        visible = {key: value for key, value in result.items() if value}
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

    # Diagnostic (2026-06-29): pinpoint "database is locked" + slow handlers. A lock error only
    # fires after the 5s busy_timeout, so the offending request shows up as a ~5s request to a
    # specific endpoint — this logs the method+path+duration so the wide-transaction/contention
    # source can be fixed precisely instead of guessed. Cheap (one monotonic clock per request).
    @app.middleware("http")
    async def _timing_and_lock_logger(request: Request, call_next):
        import time as _t
        start = _t.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 — log + re-raise, behavior unchanged
            dur_ms = int((_t.monotonic() - start) * 1000)
            if "database is locked" in str(exc).lower() or "locked" in str(exc).lower():
                logger.error(f"DB-LOCK {request.method} {request.url.path} after {dur_ms}ms: {exc}")
            else:
                logger.error(f"REQ-ERROR {request.method} {request.url.path} after {dur_ms}ms: {type(exc).__name__}: {exc}")
            raise
        dur_ms = int((_t.monotonic() - start) * 1000)
        if dur_ms >= 1000:
            logger.warning(f"SLOW-REQ {request.method} {request.url.path} {dur_ms}ms status={response.status_code}")
        if response.status_code >= 500:
            logger.error(f"REQ-5XX {request.method} {request.url.path} {dur_ms}ms status={response.status_code}")
        return response

    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(containers_router.router)

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
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
