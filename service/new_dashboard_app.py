"""Standalone replacement dashboard shell served on port 8801."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent / "new_dashboard"

app = FastAPI(
    title="AIFY Comms Dashboard Next",
    version="0.1.0",
    description="Replacement dashboard shell for aify-comms.",
    docs_url=None,
    redoc_url=None,
)

app.mount("/assets", StaticFiles(directory=APP_DIR), name="new-dashboard-assets")


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


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(APP_DIR / "index.html", media_type="text/html")


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return RedirectResponse(url="/")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse(Path(__file__).resolve().parent / "favicon.svg", media_type="image/svg+xml")
