"""The spawn spec an environment assignment writes, and the id the caller needs back.

Extracted from `assign_agent_environment` in `service/routers/agents/config.py` in v0.5.4;
`test_assign_agent_environment_split_is_inert.py` inlines it back and AST-compares against the
pre-split fixture. The body is at its original 8-space column so the SQL literals are preserved
byte-for-byte.

ASSIGNING AN ENVIRONMENT IS A SPEC WRITE. The spec is what a future spawn reads to know where and
how to start this agent, so an assignment that did not reach it would look correct in the UI and
produce a worker on the old host at the next cold start. There is no separate "create spec" step for
an agent that never had one, which is why this is an upsert rather than an update.

THE TWO BRANCHES ARE NOT SYMMETRIC and that is the part worth knowing. An UPDATE touches six columns
and MERGES metadata, preserving whatever a previous spawn recorded there. An INSERT has to supply
all eighteen, and it writes the empty-but-valid JSON defaults (`{}` / `[]`) rather than NULL --
those columns are read with `json.loads` downstream, so a NULL is a crash and not a missing value.

`spec_id` IS RETURNED RATHER THAN MUTATED: the caller records it on the agent session afterwards.
After the split it would otherwise be a HELPER local the caller still reads -- the live-out defect
the extract-method gate refuses.
"""
from __future__ import annotations

import json
import time
import uuid

from service.api_core.serialization import _json_loads_or


async def _upsert_spawn_spec_for_assignment(
    db, agent, agent_id, req, environment_id, runtime, workspace, model, runtime_config, now
):
        """Update this agent's newest spawn spec, or create one, and hand back its id.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        spec_cursor = await db.execute(
            "SELECT * FROM spawn_specs WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
        spec = await spec_cursor.fetchone()
        if spec:
            spec_id = spec["id"]
            await db.execute(
                """
                UPDATE spawn_specs
                SET environment_id = ?, runtime = ?, workspace = ?, model = ?, metadata = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    json.dumps({**_json_loads_or(spec["metadata"], {}), **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    agent_id,
                ),
            )
        else:
            spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO spawn_specs (
                    id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                    system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                    context_policy, restart_policy, metadata, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    "",
                    "managed-warm",
                    "",
                    agent["instructions"] or "",
                    "{}",
                    "[]",
                    "{}",
                    "{}",
                    "{}",
                    json.dumps({"createdBy": req.requestedBy or "dashboard", "assignedFromDashboard": True, **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    now,
                ),
            )
        return spec_id
