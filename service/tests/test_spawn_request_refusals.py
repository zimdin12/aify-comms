"""Starting a managed worker, and reporting how the start went — ten refusals, none of them tested.

The two endpoints behind the dashboard's Start button. `POST /spawn-requests` decides whether a
worker may be started at all; `PATCH /spawn-requests/{id}` is how the bridge reports back as it
claims, starts, runs, or fails. Every 4xx below was in the untested set:

    POST   400 Unsupported spawn mode "<m>"
           404 Environment "<e>" not found
           409 Environment "<e>" is <status>; restart its bridge before spawning.
           400 Environment "<e>" does not advertise runtime "<r>"
    PATCH  400 Unsupported spawn request status "<s>"
           404 Spawn request "<id>" not found
           409 Spawn request "<id>" is already <s>; late bridge update "<s>" was ignored.
           409 Spawn request "<id>" is claimed by another bridge
           500 Spawn spec "<id>" missing
           409 Spawn request "<id>" was concurrently <s>; the "<s>" update was dropped …

ALL TEN READ AS EXERCISED UNTIL fe1e22ad, because `service/tests/data/` holds verbatim pre-split
copies of both handlers and the coverage scan was reading them. Nothing asserted any of them.

THE LAST TWO ARE THE POINT OF THIS FILE. Both exist because the bridge and the operator write to the
same row from different processes:

  * `is already <s>; late bridge update` — the row reached a terminal state and a slower bridge
    message arrived afterwards. Refusing keeps a stopped worker stopped.
  * `was concurrently <s>; … dropped to avoid resurrecting a stopped worker` — the TOCTOU guard from
    bughunt 2026-07-03. The status is read ONCE at the top; an operator Stop committing between that
    read and the write would otherwise be clobbered back to `running` AFTER the PTY was spawned,
    losing the Stop and leaving a live zombie. It is tested by making the race HAPPEN — patching the
    settle step, which runs inside exactly that window, to commit the cancellation — rather than by
    asserting a `WHERE` clause exists in the source.

`_SPAWN_TERMINAL_STATUSES` IS A MISNOMER AND IS INERT, recorded here rather than changed: it holds
`running` alongside `failed` and `cancelled`, and its only use guards a line whose own inner
condition is `{failed, cancelled}`. So `running` reaching the outer check changes nothing. The
OBSERVABLE rule — `finished_at` is stamped for failed and cancelled and for nothing else — is pinned
below, which is what protects the behaviour if someone later trusts the constant's name.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

ENVIRONMENT_ID = "linux:test-host:default"
BRIDGE_ID = "bridge-one"

#: The five the PATCH allowlist accepts, and a spread of what it must not.
ACCEPTED_STATUSES = ("claimed", "starting", "running", "failed", "cancelled")
REFUSED_STATUSES = ("queued", "stopped", "done", "complete", "", "run")


class SpawnRequestRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self._heartbeat()

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _heartbeat(self, status: str = "online", runtimes=("codex",)) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": BRIDGE_ID,
                "cwdRoots": ["/workspace"],
                "runtimes": [{"runtime": r, "available": True} for r in runtimes],
                "status": status,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _create(self, **overrides):
        body = {
            "environmentId": ENVIRONMENT_ID,
            "agentId": "lc-worker",
            "runtime": "codex",
            "workspace": "/workspace/proj",
        }
        body.update(overrides)
        return self.client.post("/api/v1/spawn-requests", json=body)

    def _created_id(self) -> str:
        response = self._create()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["spawnRequest"]["id"]

    def _patch(self, spawn_request_id: str, **body):
        return self.client.patch(f"/api/v1/spawn-requests/{spawn_request_id}", json=body)

    def _row(self, spawn_request_id: str) -> dict:
        async def read():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,),
                )
                row = await cursor.fetchone()
                return dict(row) if row else {}

        return asyncio.run(read())

    def _write(self, sql: str, params: tuple) -> None:
        async def write():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(write())

    # ── POST: may this worker be started at all ──────────────────────────────────────────────

    def test_the_spawn_mode_allowlist_is_exactly_managed_warm(self):
        """One member today, asserted as a set anyway. `managed-warm` is the only mode the claim
        path knows how to serve, so a request in any other mode would sit queued forever."""
        self.assertEqual(self._create(mode="managed-warm").status_code, 200)
        for mode in ("managed", "warm", "resident", "detached", "managed_warm", "MANAGED-WARM"):
            with self.subTest(mode=mode):
                response = self._create(mode=mode)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"], f'Unsupported spawn mode "{mode.strip()}"',
                )

    def test_an_empty_mode_means_the_default_rather_than_a_refusal(self):
        """`str(req.mode or "managed-warm")` — pinned because the refusal message would otherwise
        say `Unsupported spawn mode ""`, which tells an operator nothing about what to send."""
        self.assertEqual(self._create(mode="").status_code, 200)

    def test_an_unknown_environment_is_404_and_names_it(self):
        response = self._create(environmentId="linux:nowhere:default")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["detail"], 'Environment "linux:nowhere:default" not found',
        )

    def test_an_offline_environment_is_refused_and_says_restart_the_bridge(self):
        """THE REFUSAL AN OPERATOR ACTUALLY MEETS. A spawn onto an environment whose bridge is gone
        would queue a request nothing can claim, so the message names the remedy rather than the
        state alone."""
        for status in ("offline", "degraded"):
            with self.subTest(status=status):
                self._heartbeat(status=status)
                response = self._create()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f'Environment "{ENVIRONMENT_ID}" is {status}; restart its bridge before spawning.',
                )

    def test_a_status_the_bridge_may_not_request_is_stored_as_online(self):
        """Why the loop above is only two values. `ENVIRONMENT_REGISTRABLE_STATUSES` is
        {online, degraded, offline} — `forgotten` and `disabled` are operator DECISIONS a bridge
        cannot claim, and anything else falls back to `online`. So a heartbeat saying `stale` does
        not produce a stale environment, and a test that assumed it would was asserting about a
        state the write path cannot create."""
        for requested in ("stale", "unknown", "sleeping", "forgotten", "disabled"):
            with self.subTest(requested=requested):
                self._heartbeat(status=requested)
                self.assertEqual(
                    self._create().status_code, 200,
                    f"a heartbeat claiming {requested!r} must be stored as online",
                )

    def test_an_environment_whose_bridge_WENT_SILENT_is_refused_too(self):
        """The gate reads the DERIVED status, not the stored column — which is the difference
        between "a bridge said online once" and "a bridge is online". A silent bridge ages to
        `offline` after 90s, and a spawn onto it would queue a request nothing can claim. This is
        the same false-green shape aify-doctor's `env-bridge` check was built for, on the write
        side."""
        self._heartbeat(status="online")
        self._write(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            ("2020-01-01T00:00:00Z", ENVIRONMENT_ID),
        )
        response = self._create()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            f'Environment "{ENVIRONMENT_ID}" is offline; restart its bridge before spawning.',
        )

    def test_only_an_ONLINE_environment_accepts_a_spawn(self):
        """The mirror. `online` is the single admitted value, and the comparison folds case — a
        bridge reporting `Online` must not be treated as down."""
        for status in ("online", "ONLINE", "Online"):
            with self.subTest(status=status):
                self._heartbeat(status=status)
                self.assertEqual(self._create().status_code, 200)

    def test_a_runtime_the_environment_does_not_advertise_is_refused(self):
        """`cwdRoots` bounds WHERE a worker may run; the advertised runtimes bound WHAT may run.
        Spawning a runtime the host has no CLI for produces a worker that dies on its first line."""
        self._heartbeat(runtimes=("codex",))
        for runtime in ("pi", "hermes", "opencode", "claude-code"):
            with self.subTest(runtime=runtime):
                response = self._create(runtime=runtime)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    f'Environment "{ENVIRONMENT_ID}" does not advertise runtime "{runtime}"',
                )

    def test_an_advertised_runtime_is_accepted(self):
        self._heartbeat(runtimes=("codex", "pi"))
        for runtime in ("codex", "pi"):
            with self.subTest(runtime=runtime):
                self.assertEqual(self._create(runtime=runtime).status_code, 200)

    # ── PATCH: the bridge reporting back ─────────────────────────────────────────────────────

    def test_the_status_allowlist_is_exactly_the_five_lifecycle_states(self):
        request_id = self._created_id()
        for status in REFUSED_STATUSES:
            with self.subTest(refused=status):
                response = self._patch(request_id, status=status)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"], f'Unsupported spawn request status "{status}"',
                )
        for status in ACCEPTED_STATUSES:
            with self.subTest(accepted=status):
                response = self._patch(self._created_id(), status=status)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["spawnRequest"]["status"], status)

    def test_the_status_is_normalised_before_the_allowlist_but_ECHOED_raw(self):
        """Both halves matter. The check folds case so a bridge's `"Running"` is honoured; the
        refusal echoes what the caller actually sent, because an operator debugging a rejected value
        needs to see their own spelling rather than a normalised one."""
        response = self._patch(self._created_id(), status="  RUNNING ")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["spawnRequest"]["status"], "running")
        refused = self._patch(self._created_id(), status="  Bogus ")
        self.assertEqual(refused.json()["detail"], 'Unsupported spawn request status "  Bogus "')

    def test_an_unknown_spawn_request_is_404(self):
        response = self._patch("spawn_nope", status="running")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], 'Spawn request "spawn_nope" not found')

    def test_a_late_bridge_update_after_a_terminal_status_is_refused(self):
        """A stopped worker stays stopped. Without this, a bridge message still in flight when the
        operator cancels would walk the row back to `running`."""
        for terminal in ("failed", "cancelled"):
            for late in ("claimed", "starting", "running"):
                with self.subTest(terminal=terminal, late=late):
                    request_id = self._created_id()
                    self.assertEqual(self._patch(request_id, status=terminal).status_code, 200)
                    response = self._patch(request_id, status=late)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(
                        response.json()["detail"],
                        f'Spawn request "{request_id}" is already {terminal}; late bridge update '
                        f'"{late}" was ignored.',
                    )

    def test_repeating_the_SAME_terminal_status_is_not_a_late_update(self):
        """A retrying bridge sends `failed` twice. Refusing the repeat would turn a duplicate
        delivery into an error the bridge would log and retry again."""
        request_id = self._created_id()
        self.assertEqual(self._patch(request_id, status="failed").status_code, 200)
        self.assertEqual(self._patch(request_id, status="failed").status_code, 200)

    def test_a_second_bridge_cannot_update_a_request_another_one_claimed(self):
        """Two bridges serving one environment is a real state — the whole managed fleet went down
        once from a superseding bridge. Ownership of a claimed request must not transfer silently."""
        request_id = self._created_id()
        self._write(
            "UPDATE spawn_requests SET claimed_by_bridge_id = ? WHERE id = ?",
            ("bridge-one", request_id),
        )
        response = self._patch(request_id, status="running", bridgeId="bridge-two")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            f'Spawn request "{request_id}" is claimed by another bridge',
        )

    def test_the_claiming_bridge_itself_may_update(self):
        request_id = self._created_id()
        self._write(
            "UPDATE spawn_requests SET claimed_by_bridge_id = ? WHERE id = ?",
            ("bridge-one", request_id),
        )
        self.assertEqual(
            self._patch(request_id, status="running", bridgeId="bridge-one").status_code, 200,
        )

    def test_an_update_that_names_no_bridge_is_not_treated_as_a_stranger(self):
        """`if req.bridgeId and row[...] != req.bridgeId` — an omitted bridge id skips the check
        entirely. Pinned because the alternative reading (treat absent as a mismatch) would refuse
        every dashboard-issued update on a claimed request."""
        request_id = self._created_id()
        self._write(
            "UPDATE spawn_requests SET claimed_by_bridge_id = ? WHERE id = ?",
            ("bridge-one", request_id),
        )
        self.assertEqual(self._patch(request_id, status="cancelled").status_code, 200)

    # ── the guard against resurrecting a stopped worker ──────────────────────────────────────

    def test_a_stop_committed_MID_REQUEST_is_honoured_rather_than_clobbered(self):
        """THE TOCTOU GUARD, exercised by making the race happen.

        `current_status` is read once near the top. `_settle_running_spawn` runs after that read and
        before the write, which is exactly the window an operator Stop lands in. Patching it to
        commit the cancellation reproduces the bughunt 2026-07-03 defect: without the `WHERE status
        NOT IN (...)` clause the write puts the row back to `running` after the PTY was spawned, and
        the operator's Stop is lost along with the worker it should have killed.
        """
        import service.routers.spawn_requests as module

        request_id = self._created_id()
        original = module._settle_running_spawn

        async def cancel_then_settle(db, req, row, spec_row, now, started_at, status_value,
                                     session_id, runtime_state):
            # Same connection, so the write lands inside the handler's own transaction — the
            # faithful shape of a concurrent finalize that committed first.
            await db.execute(
                "UPDATE spawn_requests SET status = 'cancelled' WHERE id = ?", (request_id,),
            )
            return await original(db, req, row, spec_row, now, started_at, status_value,
                                  session_id, runtime_state)

        module._settle_running_spawn = cancel_then_settle
        try:
            response = self._patch(request_id, status="running")
        finally:
            module._settle_running_spawn = original

        self.assertEqual(response.status_code, 409, response.text)
        # ONE CONTIGUOUS TAIL — see the note in `test_virtual_terminal_refusals.py`. Wrapping after
        # `update was ` splits the message's longest static chunk, which is what the coverage gate
        # matches on, leaving a refusal this test fully asserts counted as untested.
        self.assertEqual(
            response.json()["detail"],
            f'Spawn request "{request_id}" was concurrently cancelled; the "running'
            + '" update was dropped to avoid resurrecting a stopped worker.',
        )
        self.assertEqual(
            self._row(request_id)["status"], "cancelled",
            "the row must keep the state the concurrent Stop committed",
        )

    def test_a_missing_spawn_spec_is_a_500_and_says_which_one(self):
        """A 500 rather than a 4xx, correctly: the schema makes this state impossible — the FK is
        ON DELETE CASCADE, so deleting a spec deletes its requests — and a row that reaches it means
        the database itself is inconsistent, not that the caller did anything wrong. Reachable here
        only because the test writes it directly with foreign keys off, which is the point: the
        guard is a defence against the schema not holding, and it must still name the row.
        """
        request_id = self._created_id()
        self._write(
            "UPDATE spawn_requests SET spawn_spec_id = ? WHERE id = ?", ("spec_gone", request_id),
        )
        response = self._patch(request_id, status="running")
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(response.json()["detail"], 'Spawn spec "spec_gone" missing')

    # ── the inert constant, pinned by behaviour ──────────────────────────────────────────────

    def test_finished_at_is_stamped_for_failed_and_cancelled_and_nothing_else(self):
        """`_SPAWN_TERMINAL_STATUSES` holds `running` as well, and that membership does nothing —
        the line it guards has its own `{failed, cancelled}` condition. This asserts the behaviour
        rather than the constant, so trusting the constant's name later cannot quietly change it."""
        for status in ("claimed", "starting", "running"):
            with self.subTest(no_finish=status):
                request_id = self._created_id()
                self.assertEqual(self._patch(request_id, status=status).status_code, 200)
                self.assertFalse(
                    self._row(request_id)["finished_at"],
                    f"{status} is not a finish and must not stamp finished_at",
                )
        for status in ("failed", "cancelled"):
            with self.subTest(finished=status):
                request_id = self._created_id()
                self.assertEqual(self._patch(request_id, status=status).status_code, 200)
                self.assertTrue(self._row(request_id)["finished_at"])
