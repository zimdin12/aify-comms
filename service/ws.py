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
        # Iterate a SNAPSHOT (bughunt 2026-07-03): a concurrent disconnect() does an
        # in-place list.remove during our `await send_text`, shifting the list under an
        # index-based iterator and silently SKIPPING a live client — for streamed
        # terminal_output (~40/s) that's a sequence gap → transient scrambled console.
        for ws in list(self._connections):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def notify_agent(self, agent_id: str, event: str, data: dict = None):
        """Send to one agent's socket -- and NOTHING HAS EVER CONNECTED AS ONE.

        Measured 2026-08-26 across the repo, with both controls in the same run: the dashboard is the
        only client, it connects to `/ws` with no query parameter, and nothing anywhere connects with
        `agent_id`. So `_agents` is permanently empty and every call here returns without sending.

        Its three callers -- a new direct message, a new channel message, a dispatch request -- read
        as if the recipient is being pushed a notification. None of them is. Delivery to an agent runs
        over the dispatch claim loop on HTTP; this socket was an optimisation whose consumer was never
        written, which is exactly why nothing broke and nobody noticed.

        Kept rather than deleted: an agent-side socket would make dispatch delivery push instead of
        poll, and that is the operator's call. `test_the_agent_addressed_websocket_half_has_no_client`
        fails the day a client appears, so the three call sites get re-read at the moment they start
        meaning something.
        """
        ws = self._agents.get(agent_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"event": event, "data": data or {}}))
            except Exception:
                self.disconnect(ws)
