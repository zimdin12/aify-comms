"""An operator's change to a defined agent, asked while its host is away, reaches the host's file when
the host returns, and comes back in the host's next snapshot (P0 C4; the plan's proof for P4).

A REAL SERVICE PROCESS (`E2EStack`) and aify-env's REAL CODE (its store, its API client, its sync), run
by node from the aify-env checkout in a process of its own for each visit of the host, so nothing is
shared but the wire and the directory. The steps, each read back from where it lands:

1. The host publishes `lead` (model m1): the service holds it at revision 1, owned by the host.
2. The host is gone. The operator asks for model m2: the request is pending, the file still says m1.
3. The host comes back: the file says m2 and names the request, the request is done at revision 2,
   and the service holds the definition at revision 2.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from service.tests.e2e.harness import E2E_OPERATOR_KEY, E2EStack
from service.tests.test_agent_definition_push import ENV_REPOS

HOST_SCRIPT = Path(__file__).with_name("definition_host.mjs")
SYNC_MODULE = Path("lib") / "plugins" / "aify-comms" / "definition-sync.mjs"
OPERATOR = {"X-Aify-Operator-Key": E2E_OPERATOR_KEY}


def _host(repo: Path, stack: E2EStack, store: Path, phase: str) -> dict:
    run = subprocess.run(["node", str(HOST_SCRIPT), str(repo), stack.base_url, str(store), phase],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, f"the {phase} visit failed:\n{run.stdout}\n{run.stderr}"
    state = json.loads(run.stdout.strip().splitlines()[-1])
    assert (state["lastPushError"], state["lastRequestError"]) == ("", ""), state
    return state


def _lead(stack: E2EStack) -> dict:
    return stack.api("GET", "/api/v1/agents")["agents"]["lead"]["definition"]


def test_an_offline_request_reaches_the_hosts_file_and_comes_back(tmp_path):
    repo = next((candidate for candidate in ENV_REPOS if (candidate / SYNC_MODULE).is_file()), None)
    if repo is None:
        pytest.skip("no aify-env checkout with the definition sync; set AIFY_ENV_REPO")
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    store = tmp_path / "definitions"
    with E2EStack(data_dir=tmp_path / "stack") as stack:
        _host(repo, stack, store, "publish")
        held = _lead(stack)
        assert (held["state"], held["ownerMachineId"], held["revision"]) == ("defined", "win32:e2e-host", 1)

        asked = stack.api("POST", "/api/v1/agent-definitions/lead/requests",
                          {"patch": {"model": "m2"}, "requestedBy": "dashboard"}, headers=OPERATOR)["request"]
        assert asked["status"] == "pending"
        assert json.loads((store / "lead.json").read_text())["agent"]["model"] == "m1", "nothing reaches the host while it is away"

        state = _host(repo, stack, store, "apply")
        assert state["requestsHandled"] == 1
        written = json.loads((store / "lead.json").read_text())
        assert (written["agent"]["model"], written["appliedRequest"], written["revision"]) == ("m2", asked["id"], 2)
        [finished] = [r for r in stack.api("GET", "/api/v1/agent-definitions/lead/requests", headers=OPERATOR)["requests"]
                      if r["id"] == asked["id"]]
        assert (finished["status"], finished["resultIncarnation"], finished["resultRevision"]) == ("done", 1, 2)
        assert _lead(stack)["revision"] == 2, "the host's next snapshot carried the change back"
