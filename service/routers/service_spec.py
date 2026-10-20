"""`GET /.well-known/aify-service`: how a new host learns this service (guided install S1).

Unauthenticated by design: a host with no key can only learn a service by asking it, and the document
holds names, paths and hashes, never a value (service/api_core/service_spec.py).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from service.api_core.service_spec import served_spec
from service.config import get_config

router = APIRouter()

#: Written by scripts/stamp.sh beside `_build_stamp.json`, and copied into the image with service/.
SPEC_PATH = Path(__file__).resolve().parent.parent / "_service_spec.json"


@router.get("/.well-known/aify-service")
def service_spec():
    try:
        text = SPEC_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = None
    spec, reason = served_spec(text, get_config().build_sha)
    if spec is None:
        return JSONResponse({"error": reason}, status_code=503)
    return spec
