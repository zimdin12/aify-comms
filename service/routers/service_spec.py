"""`GET /.well-known/aify-service` and its artifact manifests: how a new host learns this service
(guided install S1).

Unauthenticated by design: a host with no key can only learn a service by asking it, and both documents
hold names, paths and hashes, never a value (service/api_core/service_spec.py).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from service.api_core.service_spec import served_spec
from service.config import get_config

router = APIRouter()

#: Written by scripts/stamp.sh beside `_build_stamp.json`, and copied into the image with service/.
SPEC_PATH = Path(__file__).resolve().parent.parent / "_service_spec.json"
MANIFEST_SUFFIX = ".manifest"


def _stamped():
    try:
        text = SPEC_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = None
    return served_spec(text, get_config().build_sha)


@router.get("/.well-known/aify-service")
def service_spec():
    stamped, reason = _stamped()
    if stamped is None:
        return JSONResponse({"error": reason}, status_code=503)
    return stamped["spec"]


@router.get("/.well-known/aify-service/artifacts/{name}")
def artifact_manifest(name: str):
    """The `sha256sum` manifest of one bound artifact, exactly the bytes its `sha256` names."""
    stamped, reason = _stamped()
    if stamped is None:
        return JSONResponse({"error": reason}, status_code=503)
    text = stamped["manifests"].get(name[:-len(MANIFEST_SUFFIX)]) if name.endswith(MANIFEST_SUFFIX) else None
    if text is None:
        return JSONResponse({"error": f"no manifest named {name!r}"}, status_code=404)
    return Response(text.encode("utf-8"), media_type="text/plain; charset=utf-8")
