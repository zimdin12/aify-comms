"""This service's roster, read by aify-env's import, gives each field the value the agent registered
with (P0 C10; P5's proof across both repos).

aify-env maps roster fields by name: `cwd`, `sessionMode`, `runtime`, `runtimeConfig.effort`,
`herdrSpace`, `machineId`. A field renamed here leaves every aify-env test green, because those tests
hand the mapper a roster written by hand. So this registers agents with a REAL SERVICE PROCESS
(`E2EStack`) and runs aify-env's own client and mapper against it by node. Every value is distinct
from what the mapper writes when a field is missing (herdrSpace is set false through the service's own
route, since the mapper's neutral value is true), so a field that never arrives cannot pass (review of
P5: the first version's herdrSpace was the fallback's value, and passed with it forced).

A second agent registered with neither a model nor a runtimeConfig shows both kinds of field as the
service really produces them: registration stores a missing model as "" (`req.model or ""`), so the
roster REPORTS it empty; no runtimeConfig means no effort is reported, so effort comes back UNREPORTED.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from service.tests.e2e.harness import E2EStack
from service.tests.test_agent_definition_push import ENV_REPOS

SCRIPT = Path(__file__).with_name("roster_as_definitions.mjs")
MAPPER = Path("lib") / "plugins" / "aify-comms" / "agent-import-records.mjs"


def test_the_roster_reads_as_definitions_field_by_field(tmp_path):
    repo = next((candidate for candidate in ENV_REPOS if (candidate / MAPPER).is_file()), None)
    if repo is None:
        pytest.skip("no aify-env checkout with the import mapper; set AIFY_ENV_REPO")
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    with E2EStack(data_dir=tmp_path / "stack") as stack:
        for body in (
            {"agentId": "lead", "role": "reviewer", "name": "The Lead", "cwd": "C:/work/lead", "model": "claude-sonnet-4-5",
             "instructions": "review only", "runtime": "claude-code", "machineId": "WIN32:E2E-Host",
             "sessionMode": "resident", "runtimeConfig": {"effort": "high"}},
            {"agentId": "elsewhere", "role": "coder", "runtime": "codex", "machineId": "win32:other-host", "sessionMode": "resident"},
            {"agentId": "plain", "role": "coder", "runtime": "generic", "machineId": "win32:e2e-host", "sessionMode": "resident"},
            {"agentId": "sparse", "role": "coder", "runtime": "codex", "machineId": "win32:e2e-host", "sessionMode": "resident"},
        ):
            stack.api("POST", "/api/v1/agents", body)
        stack.api("PATCH", "/api/v1/agents/lead/herdr-space", {"show": False})
        run = subprocess.run(["node", str(SCRIPT), str(repo), stack.base_url, "win32:e2e-host"],
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
        records = {record["id"]: record for record in json.loads(run.stdout.strip().splitlines()[-1])}

    assert sorted(records) == ["lead", "plain", "sparse"], "another machine's agent is not this host's to import"
    assert records["lead"] == {"id": "lead", "unreported": ["env"], "agent": {
        "name": "The Lead", "role": "reviewer", "harness": "claude", "mode": "resident", "workspace": "C:/work/lead",
        "model": "claude-sonnet-4-5", "effort": "high", "instructions": "review only", "env": {}, "herdrSpace": False}}
    assert records["plain"] == {"id": "plain", "notImportable": "its runtime generic has no harness"}
    sparse = records["sparse"]
    assert (sparse["unreported"], sparse["agent"]["model"], sparse["agent"]["effort"]) == (["effort", "env"], "", ""), sparse
