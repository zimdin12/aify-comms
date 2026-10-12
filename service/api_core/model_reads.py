"""What an agent's session actually runs, read by its host when someone asks (0.9.1 item 3B).

ON DEMAND, NEVER ON A TIMER (Steven, 2026-10-12: "time based stuff is bad, it should be on demand"). A read is
asked when a person looks at an agent, handed to the agent's host through a claim the host holds open
(`service/longpoll.py`), and answered from the harness's own record of that session. Nothing here runs unless
someone asks.

IN MEMORY: a read lives only while its asker waits, and the service is single-worker (CLAUDE.md), so one process
holds every read. A restart loses only reads whose askers went with it.

THE LAST ANSWER per agent is kept for a host that does not answer in time. It is returned with when it was read
and why it is not fresh, never as current.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from service import longpoll

#: How long an asker waits for the host. A read there is one file read, or one stat when the file has not changed,
#: so a healthy host answers well inside this; a host that is down costs each asker this long.
READ_WAIT_S = 3.0


def scope(machine_id: str) -> str:
    """The long-poll scope a machine's host waits on."""
    return f"model-read:{str(machine_id or '').lower()}"


def read_input(row: Any) -> dict:
    """What the host needs to find an agent's session: its runtime, workspace and native session handle. PURE."""
    return {"agentId": str(row["id"]), "runtime": str(row["runtime"] or ""), "workspace": str(row["cwd"] or ""),
            "sessionHandle": str(row["session_handle"] or "")}


def host_answer(result: Any) -> dict:
    """One agent's answer as the host may give it, `{model, at}` or `{unknown}`, and nothing else. PURE."""
    if isinstance(result, dict) and isinstance(result.get("model"), str) and result["model"]:
        return {"model": result["model"], "at": str(result.get("at") or "")}
    if isinstance(result, dict) and isinstance(result.get("unknown"), str) and result["unknown"]:
        return {"unknown": result["unknown"]}
    return {"unknown": "its host answered with no reading"}


@dataclass
class ModelRead:
    id: str
    machine: str
    agents: list
    answered: asyncio.Future


class ModelReads:
    """The reads waiting for a host, and each agent's last answer."""

    def __init__(self) -> None:
        self._open: dict[str, ModelRead] = {}
        self._unclaimed: dict[str, list[str]] = {}
        self._last: dict[str, dict] = {}

    def ask(self, machine_id: str, agents: list) -> ModelRead:
        read = ModelRead(uuid.uuid4().hex, str(machine_id).lower(), agents, asyncio.get_running_loop().create_future())
        self._open[read.id] = read
        self._unclaimed.setdefault(read.machine, []).append(read.id)
        longpoll.notify(scope(read.machine))
        return read

    def claim(self, machine_id: str) -> list[dict]:
        """The reads waiting for this machine's host, each handed out once."""
        reads = (self._open.get(read_id) for read_id in self._unclaimed.pop(str(machine_id).lower(), []))
        return [{"readId": read.id, "agents": read.agents} for read in reads if read and not read.answered.done()]

    def answer(self, machine_id: str, read_id: str, results: Any, read_at: str) -> bool:
        """Settle a read from its own machine's host. False when nobody waits for it any more, or it is not that host's."""
        read = self._open.get(read_id)
        if read is None or read.machine != str(machine_id).lower() or read.answered.done():
            return False
        results = results if isinstance(results, dict) else {}
        answers = {agent["agentId"]: {**host_answer(results.get(agent["agentId"])), "readAt": read_at}
                   for agent in read.agents}
        self._last.update(answers)
        read.answered.set_result(answers)
        return True

    def forget(self, read: ModelRead) -> None:
        self._open.pop(read.id, None)
        waiting = self._unclaimed.get(read.machine)
        if waiting and read.id in waiting:
            waiting.remove(read.id)
            if not waiting:
                self._unclaimed.pop(read.machine, None)

    async def read(self, machine_id: str, agent: dict, *, wait_s: float = READ_WAIT_S) -> dict:
        """What one agent's session runs: its host's answer; else its last answer, marked stale; else unknown."""
        read = self.ask(machine_id, [agent])
        try:
            return (await asyncio.wait_for(asyncio.shield(read.answered), wait_s))[agent["agentId"]]
        except asyncio.TimeoutError:
            pass
        finally:
            self.forget(read)
        silent = f"its host did not answer within {wait_s:g} s"
        last = self._last.get(agent["agentId"])
        if last:
            return {**last, "stale": f"{silent}; this is its answer from {last['readAt']}"}
        return {"unknown": silent}


#: The service's one set of reads (single worker, like the live-status cache).
MODEL_READS = ModelReads()
