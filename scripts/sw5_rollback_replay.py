"""The 0.9.8 switch's rollback, replayed over one private database (plan page 3, "The rollback"; SW5 review R6).

    python scripts/sw5_rollback_replay.py --reverted <a checkout of the tree the SW5 revert produces>

Two phases, each a separate interpreter importing a different tree, over the same private SQLite file:

  cutover  this checkout, the switched service. A resident agent is reported idle by aify-env, then a turn starts
           through the legacy route the launchers still post to. A second agent's run is claimed by the switched gate,
           so the database carries post-cutover run ownership.
  revert   the reverted checkout, the legacy service, in process. Over the same file, the restored readers must:
           hold a claim behind the turn that started after the cutover, keep a queueIfBusy send queued behind it,
           release and claim once the turn ends, and see the switched claim's ownership exactly.

Each phase prints one JSON line of observations; the script compares them with the expected answers and exits 1 on
any difference. `--control-no-turn` skips the turn start, so the restored hold has nothing to hold on: the replay must
then report a mismatch, which is how it shows it can say no. `--control-wrong-api-owner` makes the restored agent GET
name another bridge as the owner of the OTHER agent's run while the rows stay correct: the replay must report a
mismatch there too, so the API's ownership answer is compared and not only the rows. Nothing here starts a server,
reads the live service, or writes outside its own temporary directory.
The replay retires with the legacy readers in SW7.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
CHECKOUT = HERE.parent.parent
AGENT, OTHER = "rb-held", "rb-owner"
MACHINE = "test-host"


def _client(db_path: Path):
    """The service as the test base builds it: one app, its router, a private data dir, over `db_path`."""
    import asyncio
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from service.db import init_db
    from service.routers.api_v2 import router

    if not db_path.exists():
        asyncio.run(init_db(db_path))
    else:
        import service.db as db_module

        db_module._db_path = db_path
    app = FastAPI()
    app.state.testing = True
    app.include_router(router, prefix="/api/v1")

    app.state.ws_manager = None  # no dashboard connected: every push is skipped, as with no browser open
    app.state.config = SimpleNamespace(data_dir=str(db_path.parent))
    return TestClient(app)


def _rows(db_path: Path, query: str, params=()):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return [list(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _register(client, agent_id: str) -> None:
    client.post("/api/v1/agents", json={
        "agentId": agent_id, "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
        "machineId": MACHINE, "bridgeId": f"bridge-{agent_id}", "launchMode": "detached",
        "capabilities": ["resident-run", "resume", "interrupt"], "runtimeConfig": {"channelEnabled": True},
    }).raise_for_status()


def _queue(db_path: Path, run_id: str, agent_id: str) -> None:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
            "subject, body, priority, status, queue_if_busy, steer_if_busy, requested_at) VALUES (?, 'dashboard', ?, "
            "'start_if_possible', 'resident', 'info', 's', 'b', 'normal', 'queued', 0, 0, datetime('now'))",
            (run_id, agent_id))
        conn.commit()
    finally:
        conn.close()


def _claim(client, agent_id: str, bridge: str) -> dict:
    answer = client.post("/api/v1/dispatch/claim", json={
        "agentId": agent_id, "bridgeId": bridge, "bridgeKind": "channel-sidecar", "machineId": MACHINE,
        "executionModes": ["channel", "resident"]})
    answer.raise_for_status()
    return answer.json()


def cutover(db_path: Path, turn: bool = True) -> dict:
    """The switched service: aify-env says idle, a legacy turn starts, the other agent's run is claimed."""
    from types import SimpleNamespace

    from service.tests import env_records

    client = _client(db_path)
    case = SimpleNamespace(client=client, _db_path=db_path)
    for agent_id in (AGENT, OTHER):
        _register(client, agent_id)
    env_records.report(case, AGENT, OTHER)
    if turn:
        client.post(f"/api/v1/agents/{AGENT}/turn-start", json={}).raise_for_status()
    _queue(db_path, "run-held", AGENT)
    _queue(db_path, "run-owned", OTHER)
    owned = _claim(client, OTHER, f"channel-{OTHER}")
    return {
        "served": client.get(f"/api/v1/agents/{AGENT}").json()["agent"]["status"],
        "turn_busy": _rows(db_path, "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (AGENT,)),
        "switched_claim_of_other": (owned.get("run") or {}).get("id"),
    }


def _misreport_the_owner() -> None:
    """The negative control for the restored API's ownership: the agent GET reports another bridge as the owner of the
    OTHER agent's run while the rows stay as they are, so only the API's projection is wrong."""
    import service.api_core.dispatch_state as dispatch_state

    honest = dispatch_state._format_dispatch_state

    def misreported(active_row, queued_count):
        state = honest(active_row, queued_count)
        if state["activeRun"] and state["activeRun"]["runId"] == "run-owned":
            state["activeRun"] = {**state["activeRun"], "claimBridgeId": "channel-somebody-else"}
        return state
    dispatch_state._format_dispatch_state = misreported


def revert(db_path: Path, wrong_api_owner: bool = False) -> dict:
    """The legacy service, over the database the switched one left."""
    client = _client(db_path)
    held = _claim(client, AGENT, f"channel-{AGENT}")
    sent = client.post("/api/v1/messages/send", json={
        "from_agent": OTHER, "to": AGENT, "type": "info", "subject": "after this turn", "body": "queued",
        "trigger": True, "queueIfBusy": True}).json()
    queued_behind = _rows(db_path, "SELECT status FROM dispatch_runs WHERE target_agent = ? AND id != 'run-held'",
                          (AGENT,))
    client.post(f"/api/v1/agents/{AGENT}/turn-end", json={}).raise_for_status()
    released = _claim(client, AGENT, f"channel-{AGENT}")
    if wrong_api_owner:
        _misreport_the_owner()
    other_run = client.get(f"/api/v1/agents/{OTHER}").json()["agent"]["dispatchState"]["activeRun"] or {}
    return {
        "held_claim": held,
        "send_stored": bool(sent.get("stored")),
        "queued_behind_turn": queued_behind,
        "released_claim": (released.get("run") or {}).get("id"),
        "ownership": _rows(db_path, "SELECT id, status, claim_bridge_id FROM dispatch_runs WHERE id IN "
                                    "('run-held', 'run-owned') ORDER BY id"),
        # The restored API's own answer about the OTHER agent's run, beside the rows above: the run, its status and
        # the bridge that owns it, exactly as the reverted agent GET returns them.
        "other_active_run": {key: other_run.get(key) for key in ("runId", "status", "claimBridgeId")},
    }


EXPECTED_CUTOVER = {"served": "online", "turn_busy": [[1]], "switched_claim_of_other": "run-owned"}
EXPECTED_REVERT = {
    "held_claim": {"ok": True, "run": None},
    "send_stored": True,
    "queued_behind_turn": [["queued"]],
    "released_claim": "run-held",
    "ownership": [["run-held", "claimed", f"channel-{AGENT}"], ["run-owned", "claimed", f"channel-{OTHER}"]],
    "other_active_run": {"runId": "run-owned", "status": "claimed", "claimBridgeId": f"channel-{OTHER}"},
}


def _phase(tree: Path, phase: str, db_path: Path, *extra: str) -> dict:
    done = subprocess.run([sys.executable, str(HERE), "--phase", phase, "--db", str(db_path), *extra], cwd=tree,
                          env={**__import__("os").environ, "PYTHONPATH": str(tree)}, capture_output=True, text=True)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if done.returncode != 0 or not lines:
        raise SystemExit(f"{phase} phase failed in {tree} (exit {done.returncode}):\n{done.stdout[-2000:]}"
                         f"\n{done.stderr[-4000:]}")
    return json.loads(lines[-1])


def _identity(tree: Path) -> dict:
    """The checkout a phase ran: its commit, its tree, and whether its working files differ from that commit (a dirty
    checkout is not the commit it names)."""
    def git(*args):
        return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reverted", type=Path)
    parser.add_argument("--phase", choices=("cutover", "revert"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--control-no-turn", action="store_true")
    parser.add_argument("--control-wrong-api-owner", action="store_true")
    args = parser.parse_args()
    if args.phase:
        observed = (cutover(args.db, not args.control_no_turn) if args.phase == "cutover"
                    else revert(args.db, args.control_wrong_api_owner))
        print(json.dumps(observed))
        return 0
    if not args.reverted or not (args.reverted / "service").is_dir():
        parser.error("--reverted must name a checkout of the reverted tree")
    if (args.reverted / "service" / "api_core" / "switched_view.py").exists():
        parser.error("--reverted still has the switch in it: check out the tree the revert produces")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "rollback-replay.db"
        no_turn = ("--control-no-turn",) if args.control_no_turn else ()
        wrong_owner = ("--control-wrong-api-owner",) if args.control_wrong_api_owner else ()
        observed = {"cutover": _phase(CHECKOUT, "cutover", db_path, *no_turn),
                    "revert": _phase(args.reverted, "revert", db_path, *wrong_owner)}
    expected = {"cutover": EXPECTED_CUTOVER, "revert": EXPECTED_REVERT}
    print(json.dumps({"switched": _identity(CHECKOUT), "reverted": _identity(args.reverted), "observed": observed,
                      "expected": expected, "match": observed == expected}, indent=1))
    return 0 if observed == expected else 1


if __name__ == "__main__":
    sys.exit(main())
