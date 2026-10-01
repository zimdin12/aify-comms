"""The bodies a host sends about its agent definitions (P0 C3, C4): the names, not the rules.

EVERY FIELD IS `Any`. The rules live in api_core (`definition_push`, `definition_requests`), which
refuse a wrong value BY NAME; a typed field here would answer 422 first and the name would be lost.
What these models add is the declaration: a key the host sends that is named nowhere here is one the
service never reads, and `test_the_env_plugin_addresses_routes_this_service_serves.py` fails on it.

Handlers read `model_dump(exclude_unset=True)`, so api_core sees exactly the keys that were sent.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DefinitionPush(BaseModel):
    bridgeId: Any = None
    machineId: Any = None
    storeId: Any = None
    revision: Any = None
    snapshotDigest: Any = None
    entries: Any = None


class DefinitionClaim(BaseModel):
    bridgeId: Any = None
    machineId: Any = None


class DefinitionResult(BaseModel):
    bridgeId: Any = None
    machineId: Any = None
    status: Any = None
    outcome: Any = None
    resultIncarnation: Any = None
    resultRevision: Any = None
