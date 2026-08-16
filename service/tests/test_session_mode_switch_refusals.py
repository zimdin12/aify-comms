"""Flipping an agent between resident and managed — three refusals, one of them about a typo.

`PATCH /agents/{id}/session-mode` is the operator override for what the wrapper auto-detects with
`[ -t 0 ]`. Three of its refusals had no test, and all three read as exercised until fe1e22ad
because `service/tests/data/` holds a pre-split copy of the handler:

    400 mode must be 'resident' or 'managed'
    409 resident mode is not supported for <r>; it is managed-only. …
    409 Agent has an active dispatch run (runId=<id>); wait for it to finish or pass force=true

THE FIRST ONE VALIDATES THE RAW VALUE, AND THAT IS LOad-BEARING. `_normalize_session_mode` fails
toward "resident" — `"bogus"`, `"managed-warm"` and `""` all come back as `resident`. If the check
read the normalised value instead of `req.mode`, every typo would silently move the agent to
resident: for a pi or opencode agent that is the managed-only limbo the second refusal exists to
prevent, reached by misspelling a word. The handler gets this right; the test exists so a
simplification cannot quietly reverse it.

FORCE IS A REAL PATH, NOT AN ESCAPE HATCH TO IGNORE. Both 409s take `force=true`, and each has a
different consequence: forcing a managed-only runtime to resident leaves the agent presence-only
with every dispatch rejected, and forcing past an active run abandons live work. Both are asserted
to succeed AND to say what they cost, because a force that silently succeeds is how an operator
finds out later.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.api_core.runtime import _SESSION_MODES, _normalize_session_mode
from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

#: Sourced from the adapters rather than re-typed: `supports_resident` is what the gate reads.
MANAGED_ONLY_RUNTIMES = ("pi", "opencode")
RESIDENT_CAPABLE_RUNTIMES = ("claude-code", "codex", "hermes")


class SessionModeSwitchRefusalTests(FastApiTestCase):
    def _register(self, agent_id: str, runtime: str, session_mode: str = "managed") -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": runtime,
                "sessionMode": session_mode,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _read(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                row = await cursor.fetchone()
                return dict(row) if row else {}

        return asyncio.run(run())

    def _switch(self, agent_id: str, mode, force: bool = False):
        body = {"mode": mode, "requestedBy": "operator"}
        if force:
            body["force"] = True
        return self.client.patch(f"/api/v1/agents/{agent_id}/session-mode", json=body)

    def _seed_active_run(self, agent_id: str, status: str = "running") -> str:
        run_id = f"run-{agent_id}-{status}"
        self._write(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at)"
            " VALUES (?,?,?,?,?)",
            (run_id, "operator", agent_id, status, "2026-08-16T00:00:00Z"),
        )
        return run_id

    # ── the mode vocabulary ──────────────────────────────────────────────────────────────────

    def test_the_mode_allowlist_is_exactly_resident_and_managed(self):
        self._register("lc-codex", "codex")
        self.assertEqual(sorted(_SESSION_MODES), ["managed", "resident"])
        for mode in ("managed-warm", "detached", "auto", "", "residentt"):
            with self.subTest(mode=mode):
                response = self._switch("lc-codex", mode)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"], "mode must be 'resident' or 'managed'",
                )

    def test_a_typo_is_refused_rather_than_silently_meaning_resident(self):
        """THE ONE THAT MATTERS HERE. `_normalize_session_mode` returns `resident` for anything it
        does not recognise, so validating the NORMALISED value would turn every typo into a real
        mode change. Asserted on the stored row, not just the status code: a 400 that had already
        written would still be a switch."""
        self.assertEqual(_normalize_session_mode("residentt"), "resident")
        self._register("lc-codex", "codex", session_mode="managed")
        self.assertEqual(self._switch("lc-codex", "residentt").status_code, 400)
        self.assertEqual(
            self._read("SELECT session_mode FROM agents WHERE id = ?", ("lc-codex",))["session_mode"],
            "managed",
            "a refused switch must leave the agent where it was",
        )

    def test_case_and_whitespace_are_tolerated(self):
        self._register("lc-codex", "codex", session_mode="managed")
        response = self._switch("lc-codex", "  RESIDENT ")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mode"], "resident")

    def test_switching_to_the_mode_it_is_already_in_is_a_no_op(self):
        self._register("lc-codex", "codex", session_mode="managed")
        response = self._switch("lc-codex", "managed")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["changed"], False)

    # ── runtimes that have no resident bridge ────────────────────────────────────────────────

    def test_a_managed_only_runtime_cannot_be_switched_to_resident(self):
        """pi and opencode have no resident bridge. The flip would produce a `presence-only` agent
        whose every dispatch is rejected as undeliverable — silent, which is why the guard exists."""
        for runtime in MANAGED_ONLY_RUNTIMES:
            with self.subTest(runtime=runtime):
                agent_id = f"lc-{runtime}"
                self._register(agent_id, runtime)
                response = self._switch(agent_id, "resident")
                self.assertEqual(response.status_code, 409, response.text)
                # The static tail contiguous — see the note beside PHRASE_PREFIX in
                # `test_every_refusal_is_exercised.py`. Wrapping after "Keep this " split the
                # message's longest chunk inside the 40 characters the coverage gate matches, so
                # this refusal stayed counted as untested while this line asserted all of it.
                self.assertEqual(
                    response.json()["detail"],
                    f"resident mode is not supported for {runtime}"
                    + "; it is managed-only. Keep this agent managed, or pass force=true to change "
                    "metadata only (it will be undeliverable).",
                )
                self.assertEqual(
                    self._read(
                        "SELECT session_mode FROM agents WHERE id = ?", (agent_id,),
                    )["session_mode"],
                    "managed",
                )

    def test_forcing_a_managed_only_runtime_resident_works_and_says_what_it_costs(self):
        """The operator may override — but a force that succeeds silently is how they find out
        later. The warning has to reach the response."""
        self._register("lc-pi", "pi")
        response = self._switch("lc-pi", "resident", force=True)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["mode"], "resident")
        # THE NAMED FIELD, not a substring search over the whole payload. My first version asserted
        # `"presence-only" in str(payload)` and passed for the wrong reason: the phrase also appears
        # as the agent's `wakeMode`, so the assertion would have survived the warning being dropped
        # entirely — which is the only thing this test is about.
        self.assertEqual(
            payload["warning"],
            "resident mode is not supported for pi (managed-only); forced switch leaves this agent "
            "presence-only and every dispatch will be rejected until it is switched back to managed.",
        )
        self.assertEqual(
            payload["agent"]["wakeMode"], "presence-only",
            "and the state the warning describes is real, not just narrated",
        )

    def test_a_resident_capable_runtime_switches_without_force(self):
        for runtime in RESIDENT_CAPABLE_RUNTIMES:
            with self.subTest(runtime=runtime):
                agent_id = f"lc-cap-{runtime}"
                self._register(agent_id, runtime)
                response = self._switch(agent_id, "resident")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["mode"], "resident")

    def test_switching_TO_managed_is_never_blocked_by_resident_support(self):
        """The gate is one-directional. Every runtime can be managed — that is the whole point of
        pi and opencode being managed-only."""
        for runtime in MANAGED_ONLY_RUNTIMES + RESIDENT_CAPABLE_RUNTIMES:
            with self.subTest(runtime=runtime):
                agent_id = f"lc-tomanaged-{runtime}"
                self._register(agent_id, runtime, session_mode="resident")
                self.assertEqual(self._switch(agent_id, "managed").status_code, 200)

    # ── live work in flight ──────────────────────────────────────────────────────────────────

    def test_an_active_dispatch_run_blocks_the_switch_and_names_it(self):
        """Flipping the mode under a running turn is how work gets lost, so the refusal carries the
        runId the operator has to wait for."""
        for status in ("claimed", "running"):
            with self.subTest(status=status):
                agent_id = f"lc-busy-{status}"
                self._register(agent_id, "codex")
                run_id = self._seed_active_run(agent_id, status)
                response = self._switch(agent_id, "resident")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f"Agent has an active dispatch run (runId={run_id}); wait for it to finish or "
                    "pass force=true",
                )

    def test_a_FINISHED_run_does_not_block_the_switch(self):
        """The mirror, over the statuses a run ends in. A completed turn blocking a mode switch
        would make the button permanently dead for any agent that has ever been dispatched to."""
        for status in ("completed", "failed", "cancelled", "queued"):
            with self.subTest(status=status):
                agent_id = f"lc-done-{status}"
                self._register(agent_id, "codex")
                self._seed_active_run(agent_id, status)
                response = self._switch(agent_id, "resident")
                self.assertEqual(response.status_code, 200, response.text)

    def test_force_overrides_the_active_run_block(self):
        self._register("lc-forced", "codex")
        self._seed_active_run("lc-forced")
        response = self._switch("lc-forced", "resident", force=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mode"], "resident")

    def test_the_switch_is_recorded_as_an_audit_event(self):
        """`mode_switch_<old>_to_<new>` in `dispatch_events` is the whole audit trail for this
        endpoint — there is no separate table — so an operator flip that left no row would be
        invisible after the fact."""
        self._register("lc-audit", "codex", session_mode="managed")
        self.assertEqual(self._switch("lc-audit", "resident").status_code, 200)
        row = self._read(
            "SELECT event_type, body FROM dispatch_events WHERE event_type LIKE 'mode_switch%'"
            " ORDER BY created_at DESC",
        )
        self.assertEqual(row.get("event_type"), "mode_switch_managed_to_resident")
        self.assertIn("lc-audit", row.get("body") or "")
        self.assertIn("operator", row.get("body") or "")
