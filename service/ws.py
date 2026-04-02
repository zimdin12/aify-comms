"""WebSocket connection manager for real-time dashboard updates and agent presence."""
import json
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._agents: dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, agent_id: str = None):
        await ws.accept()
        self._connections.append(ws)
        if agent_id:
            self._agents[agent_id] = ws

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)
        self._agents = {k: v for k, v in self._agents.items() if v != ws}

    def online_agents(self) -> set:
        return set(self._agents.keys())

    def active_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, event: str, data: dict = None):
        msg = json.dumps({"event": event, "data": data or {}})
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def notify_agent(self, agent_id: str, event: str, data: dict = None):
        ws = self._agents.get(agent_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"event": event, "data": data or {}}))
            except Exception:
                self.disconnect(ws)
