"""Where a resident agent lands when it is switched to managed.

Extracted from `switch_agent_session_mode` in `service/routers/agents/session_mode.py` in v0.5.4;
`test_switch_agent_session_mode_split_is_inert.py` inlines it back and AST-compares against the
pre-split fixture. The body is at its original 8-space column so the SQL literals are preserved
byte-for-byte.

THIS IS A DERIVATION, NOT A GATE, which is why it is not in `session_mode_gates.py` beside the other
two extractions from the same handler. It refuses nothing; it answers a question the switch cannot
proceed without and that the operator should not have to answer by hand.

THE DEFECT IT EXISTS FOR (2026-06-12, operator-reported): flipping resident to managed left
`runtime_state` with no `environmentId`, so the agent rendered in the Sessions page "unassigned"
group and looked unreachable until someone hand-edited the identity or a spawn re-bound it. Nothing
raised. Two sources are tried in order — the agent's own latest session row, then the newest
online-first environment registered for its machine — and when neither answers, the switch still
succeeds and says so in a warning rather than leaving the operator to discover the gap from the UI.

THE `except Exception` IS DELIBERATE and is why this reads as advisory rather than authoritative: a
binding that cannot be inferred must not fail a switch the operator explicitly asked for.
"""
from __future__ import annotations

from service.api_core.serialization import _normalize_machine_id


async def _infer_environment_binding_for_managed_switch(
    db, agent_id, row, new_mode, runtime_state, switch_warnings
) -> None:
        """Bind the agent to an environment, or record why it could not be bound.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Every argument
        is passed under the caller's own name for the same reason: inline-back does not substitute
        arguments.

        `runtime_state` and `switch_warnings` are MUTATED in place rather than returned. That is not
        a style choice -- returning them would change the call site, and the round trip only closes
        for a byte-identical body over a same-name call.
        """
        if new_mode == "managed" and not str(runtime_state.get("environmentId") or "").strip():
            # ENV-BINDING INFERENCE (2026-06-12, operator-reported): flipping resident→managed
            # left runtime_state without an environmentId — the agent then rendered in the
            # Sessions page's "unassigned" group and looked unreachable until the operator
            # hand-edited the identity or a spawn re-bound it. The right binding is almost
            # always derivable: the latest session row's environment, else the (online-first,
            # newest) environment registered for the agent's own machine.
            inferred_env = ""
            # `started_at`, NOT `created_at`. `agent_sessions` has no `created_at` column and never
            # has, so this statement raised `no such column: created_at` on every call from
            # 2026-06-12 to 2026-08-29 -- 78 days in which the inference this function exists for
            # could not run once. The `except Exception` below caught it, so nothing was logged and
            # the switch carried on with an empty binding: exactly the "unassigned" agent the
            # docstring says this was built to stop.
            #
            # `started_at` is NOT NULL, which is what makes the COALESCE meaningful rather than
            # decorative.
            try:
                _ls = await (await db.execute(
                    "SELECT environment_id FROM agent_sessions WHERE agent_id = ? "
                    "AND COALESCE(environment_id, '') != '' "
                    "ORDER BY datetime(COALESCE(last_seen, started_at)) DESC LIMIT 1",
                    (agent_id,),
                )).fetchone()
                inferred_env = str((_ls["environment_id"] if _ls else "") or "").strip()
            except Exception:
                inferred_env = ""
            # THE SECOND SOURCE GETS ITS OWN GUARD. Both lived in one `try`, so a failure in the
            # first skipped the second entirely -- and for 78 days the first always failed. The
            # docstring says "two sources are tried in order"; this is what makes that true.
            if not inferred_env:
                try:
                    _machine = _normalize_machine_id(row["machine_id"] or "")
                    if _machine:
                        _er = await (await db.execute(
                            "SELECT id FROM environments WHERE machine_id = ? "
                            "AND status NOT IN ('forgotten', 'disabled') "
                            "ORDER BY CASE WHEN status = 'online' THEN 0 ELSE 1 END, "
                            "datetime(COALESCE(last_seen, '')) DESC LIMIT 1",
                            (_machine,),
                        )).fetchone()
                        inferred_env = str((_er["id"] if _er else "") or "").strip()
                except Exception:
                    inferred_env = ""
            if inferred_env:
                runtime_state["environmentId"] = inferred_env
            else:
                switch_warnings.append(
                    "No environment binding could be inferred for this machine — the agent "
                    "will appear under 'unassigned' on the Sessions page until an environment "
                    "bridge for its machine comes online."
                )
