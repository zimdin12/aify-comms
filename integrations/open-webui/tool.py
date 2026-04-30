"""
Open WebUI Tool - Legacy Aify Service Integration

Legacy reference for an older container-service API. It is not the current
aify-comms dashboard/control-plane integration.

Historical install path for the old service API:
1. Go to Workspace -> Functions -> Add Function
2. Paste this file
3. Configure SERVICE_URL in the valves
4. Enable for your models
"""

import json
from typing import Optional
from pydantic import BaseModel, Field


class Tools:
    """Aify Service tools for Open WebUI."""

    class Valves(BaseModel):
        SERVICE_URL: str = Field(
            default="http://localhost:8800",
            description="Base URL of the aify service",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _api(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Call service REST API using httpx (available in Open WebUI)."""
        import httpx
        url = f"{self.valves.SERVICE_URL}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                resp = await client.get(url)
            else:
                resp = await client.post(url, json=body or {})
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}: {resp.text}"}
            return resp.json()

    async def service_info(self) -> str:
        """Get service info including available containers and endpoints."""
        return json.dumps(await self._api("GET", "/info"), indent=2)

    async def list_containers(self) -> str:
        """List all managed containers with their status, GPU allocation, and groups."""
        return json.dumps(await self._api("GET", "/api/v1/containers"), indent=2)

    async def start_container(self, name: str) -> str:
        """Start a managed sub-container by name."""
        return json.dumps(await self._api("POST", f"/api/v1/containers/{name}/start"), indent=2)

    async def stop_container(self, name: str) -> str:
        """Stop a running sub-container by name."""
        return json.dumps(await self._api("POST", f"/api/v1/containers/{name}/stop"), indent=2)

    async def gpu_status(self) -> str:
        """Get GPU device allocation showing which containers use which GPUs."""
        return json.dumps(await self._api("GET", "/api/v1/gpu"), indent=2)
