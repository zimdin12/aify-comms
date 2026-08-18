"""Two functions build the engine's StatusInputs. One of them says they must be identical.

`_compute_live_status_cache` assembles StatusInputs inline so `_refresh_agent_live_state` can derive
the served status with a pure `derive()` call instead of re-running the expensive
`_gather_status_inputs` double-gather (the 10x idle-CPU regression). Its comment states the contract:

    This MUST produce the same StatusInputs _gather_status_inputs does — the field
    semantics below mirror it exactly (see _gather_status_inputs).

and then enumerates six field semantics that have to line up. The cheap one is the SERVED path under
`status_engine=new`, so if it drifts, production status is wrong while the authoritative function
stays right — and `derive()` is the sole authority on status, which makes its INPUT the thing that
has to be trusted.

WHAT WAS ALREADY CHECKED, AND WHY IT IS NOT THIS. `StatusEngineHotRefreshParityTests` asserts parity
on the DERIVED STATUS in four hand-picked scenarios (resident working, managed available, wake
disabled, resident stale/offline). That is weaker in two ways: two different StatusInputs can derive
to the same status, so a field can diverge invisibly; and four scenarios leave most of the state
space untouched. Nothing compared the StatusInputs themselves.

Measured result when this file was written: the two producers agree on every in-scope case below.
This is a pin, not a fix — the same reason the loop-gate coverage test exists.

SCOPE. A row whose `status` is in `_MANUAL_STATUSES` makes the cheap path short-circuit and return
the manual status WITHOUT deriving, which is correct and deliberate — a manually-set status is not a
derivation. Those rows are excluded here rather than silently passing, and the exclusion is asserted
so it cannot quietly swallow the whole matrix.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3

from service.api_core.status_inputs import _compute_live_status_cache, _gather_status_inputs
from service.db import get_db
from service.tests._base import FastApiTestCase

OLD = "2020-01-01T00:00:00Z"
FRESH = "2099-01-01T00:00:00Z"

#: mode x disabled x in_turn/awaiting x worker/env (managed) x bridge freshness (resident).
CASES: dict[str, dict] = {
    "resident-plain":        dict(mode="resident"),
    "resident-wake-none":    dict(mode="resident", wake_none=True),
    "resident-in-turn":      dict(mode="resident", in_turn=1),
    "resident-in-turn-stale": dict(mode="resident", in_turn=1, last_event=OLD),
    "resident-awaiting":     dict(mode="resident", in_turn=1, awaiting=1),
    "resident-session":      dict(mode="resident", session=True),
    "managed-plain":         dict(mode="managed"),
    "managed-session":       dict(mode="managed", session=True),
    "managed-env-offline":   dict(mode="managed", session=True, env_online=False),
    "managed-worker-live":   dict(mode="managed", session=True, terminal=True),
    "managed-worker-live-env-offline": dict(
        mode="managed", session=True, terminal=True, env_online=False),
    # M2 (external review 2026-08-18). These two cases are why the file exists and why it did not
    # earn its keep for two months: the comparison below is over EVERY field, but no case produced a
    # `config_defect` or a `spawn_starting`, so those two agreed at their defaults while the cheap
    # path did not compute them AT ALL. It served `available` where the authoritative path said
    # `misconfigured` — false-available on the primary roster path, which is a routing bug because
    # `available` is documented as deliverable and promises a cold start.
    #
    # A field that is never non-default in any case is a field this file does not check.
    "managed-unlaunchable-runtime": dict(mode="managed", session=True, runtime="generic"),
    # …and the other three fields the cheap producer was silently defaulting. Each of these caused a
    # mutation to SURVIVE until it was added, which is the only proof that a case exercises anything.
    "managed-spawn-starting": dict(mode="managed", session=True, spawn_starting=True),

    "managed-unlaunchable-no-session": dict(mode="managed", runtime="generic"),
    "managed-in-turn":       dict(mode="managed", session=True, in_turn=1),
    "managed-awaiting":      dict(mode="managed", session=True, in_turn=1, awaiting=1),
    # WS-5 parity: in_turn set, awaiting_input NOT set in agent_status_state, and a console tail
    # that awaits operator input. Both builders must reach `_agent_awaiting_input` and agree.
    # THIS CASE WAS MISSING from the first version of this file, and its absence was invisible:
    # every other `awaiting` case sets the column directly, so `if in_turn and not awaiting` never
    # ran and dropping that call entirely still passed. A field varying is not the same as the code
    # that computes it being reached — which is why the anti-vacuity check below could not catch it.
    "managed-awaiting-from-console": dict(
        mode="managed", session=True, terminal=True, in_turn=1, awaiting=0,
        terminal_output="Apply this change? (y/n)"),
    "managed-wake-none":     dict(mode="managed", session=True, wake_none=True),
}
#: KNOWN DIVERGENCE, pinned rather than hidden (M2, 2026-08-18). `_gather_status_inputs` sets
#: `config_defect` for a resident whose wake mode ends in `-missing-handle`; the cheap producer does
#: not, so the authoritative path derives `misconfigured` and the served one derives `offline`.
#:
#: Making them agree is one line and is deliberately NOT done here: it moves every
#: resident-without-a-wake-handle out of the unreachable family, and
#: `test_resident_hermes_missing_handle_status` asserts the dashboard dot and the label agree within
#: that family — so a whole class of agents changes badge colour. comms-senior-dev ruled M2 belongs
#: in the same slice as the 10a `available` semantics, which awaits an operator ruling.
#:
#: The test below asserts these cases DO diverge, on exactly this field. When 10a is settled and the
#: producers are unified, that assertion fails and tells you to delete this list — which is the
#: opposite of a skip, and the reason it is written this way.
DIVERGENT_CASES: dict[str, dict] = {
    "resident-codex-missing-handle": dict(mode="resident", runtime="codex"),
    "resident-hermes-missing-handle": dict(mode="resident", runtime="hermes"),
}

#: Excluded by the manual-status short-circuit, asserted below so the exclusion stays honest.
MANUAL_CASES: dict[str, dict] = {
    "resident-stopped": dict(mode="resident", stopped=True),
    "managed-stopped":  dict(mode="managed", session=True, stopped=True),
}


class StatusInputsProducersAgreeTests(FastApiTestCase):
    DB_NAME = "aify-status-inputs-agreement-test.db"

    def _seed(self, agent_id: str, *, mode: str, runtime: str = "claude-code", **kw) -> None:
        r = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": runtime, "sessionMode": mode,
            "machineId": "linux:test", "bridgeId": f"b_{agent_id}"})
        self.assertEqual(r.status_code, 200, r.text)
        conn = sqlite3.connect(str(self._db_path))
        try:
            if kw.get("stopped"):
                conn.execute("UPDATE agents SET status='stopped' WHERE id=?", (agent_id,))
            if kw.get("wake_none"):
                conn.execute("UPDATE agents SET launch_mode='none' WHERE id=?", (agent_id,))
            if kw.get("spawn_starting"):
                # `_managed_spawn_is_starting` requires a CLAIMED spawn (status running + started_at)
                # inside its window — a queued-but-unclaimed one deliberately does not count, which is
                # the distinction that made the stuck-spawn deadlock visible as `available`.
                import datetime as _dt
                _fresh = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO spawn_requests (id, spawn_spec_id, created_by, environment_id,"
                    " agent_id, role, runtime, status, created_at, updated_at, claimed_at, started_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"spawn_{agent_id}", f"spec_{agent_id}", "tester", f"e_{agent_id}", agent_id,
                     "coder", runtime, "running", _fresh, _fresh, _fresh, _fresh))
            if "in_turn" in kw or "awaiting" in kw:
                conn.execute(
                    "INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, last_event_at)"
                    " VALUES (?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET in_turn=excluded.in_turn,"
                    " awaiting_input=excluded.awaiting_input, last_event_at=excluded.last_event_at",
                    (agent_id, int(kw.get("in_turn", 0)), int(kw.get("awaiting", 0)),
                     kw.get("last_event", FRESH)))
            if kw.get("session"):
                eid, sid, tid = f"e_{agent_id}", f"s_{agent_id}", f"t_{agent_id}"
                conn.execute(
                    "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen)"
                    " VALUES (?,?,?,?,?)",
                    (eid, "linux:test", f"eb_{agent_id}", OLD,
                     FRESH if kw.get("env_online", True) else OLD))
                conn.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status,"
                    " workspace, started_at, last_seen, owner_mode, terminal_id, terminal_status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, agent_id, eid, runtime, "running", "/w", OLD, FRESH,
                     "managed" if mode == "managed" else "resident",
                     tid if kw.get("terminal") else "", "attached" if kw.get("terminal") else ""))
                if kw.get("terminal"):
                    conn.execute(
                        "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id,"
                        " bridge_id, runtime, workspace, command, status, requested_by, created_at,"
                        " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, sid, agent_id, eid, f"eb_{agent_id}", runtime, "/w", "x", "attached",
                         "dashboard", OLD, FRESH))
                    if kw.get("terminal_output"):
                        conn.execute("UPDATE terminal_sessions SET output=? WHERE id=?",
                                     (kw["terminal_output"], tid))
            conn.commit()
        finally:
            conn.close()

    def _both(self, agent_id: str) -> tuple[dict | None, dict | None]:
        """(authoritative, cheap) as plain dicts; cheap is None on the manual short-circuit."""
        async def _run():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
                auth = await _gather_status_inputs(db, row)
                payload = await _compute_live_status_cache(db, row)
                cheap = payload.get("status_inputs")
                return (
                    dataclasses.asdict(auth),
                    dataclasses.asdict(cheap) if cheap is not None else None,
                )
            finally:
                await db.close()

        return asyncio.run(_run())

    # ── the contract ─────────────────────────────────────────────────────────────────────────

    def test_the_KNOWN_DIVERGENCE_is_still_exactly_where_it_is_documented(self):
        """A pin, not a skip. If this fails, the producers were unified — delete DIVERGENT_CASES and
        move these into CASES. If it fails the OTHER way (a new field diverges too), the pin has
        stopped describing reality and the divergence has spread."""
        for name, kw in DIVERGENT_CASES.items():
            with self.subTest(case=name):
                agent_id = f"sid-{name}"
                self._seed(agent_id, **kw)
                auth, cheap = self._both(agent_id)
                self.assertIsNotNone(cheap, f"{name}: the cheap path short-circuited unexpectedly")
                diff = {k for k in auth if auth[k] != cheap[k]}
                self.assertEqual(
                    diff, {"config_defect"},
                    f"{name}: the documented divergence is config_defect ALONE. Got {sorted(diff)}. "
                    f"If it is now empty the producers agree — delete DIVERGENT_CASES and move these "
                    f"cases into CASES. If it grew, a second field started diverging and the M2 class "
                    f"is back.",
                )
                self.assertTrue(
                    auth["config_defect"] and not cheap["config_defect"],
                    f"{name}: expected the AUTHORITATIVE producer to carry the defect and the served "
                    f"one to miss it; got auth={auth['config_defect']!r} cheap={cheap['config_defect']!r}",
                )

    def test_both_producers_build_the_same_status_inputs(self):
        for name, kw in CASES.items():
            with self.subTest(case=name):
                agent_id = f"sia-{name}"
                self._seed(agent_id, **kw)
                auth, cheap = self._both(agent_id)
                self.assertIsNotNone(
                    cheap,
                    f"{name}: the cheap path short-circuited on a manual status. If that is now "
                    f"correct for this case, move it to MANUAL_CASES; otherwise the served path "
                    f"stopped deriving for a state that must be derived.",
                )
                diff = {k: (auth[k], cheap[k]) for k in auth if auth[k] != cheap[k]}
                self.assertEqual(
                    diff, {},
                    f"{name}: the SERVED StatusInputs (status_engine=new) disagrees with the "
                    f"authoritative _gather_status_inputs. derive() is the sole authority on status, "
                    f"so a divergence here is a wrong status in production while the authoritative "
                    f"function stays right. Fields shown as (gather, cheap).",
                )

    # ── the exclusion, kept honest ───────────────────────────────────────────────────────────

    def test_a_manual_status_short_circuits_and_is_excluded_deliberately(self):
        """The cheap path returns a manually-set status without deriving. That is correct — but if
        it ever applied to the whole matrix, the test above would pass by comparing nothing."""
        for name, kw in MANUAL_CASES.items():
            with self.subTest(case=name):
                agent_id = f"siam-{name}"
                self._seed(agent_id, **kw)
                _, cheap = self._both(agent_id)
                self.assertIsNone(cheap, f"{name}: expected the manual-status short-circuit")

    # ── anti-vacuity: the matrix must actually move the fields ───────────────────────────────

    def test_the_matrix_exercises_every_field_in_both_directions(self):
        """Agreement is trivial if every case produces identical inputs. Each field the contract
        enumerates must take BOTH values somewhere in the matrix, or this file is comparing
        constants and would keep passing through a real drift."""
        seen: dict[str, set] = {}
        for name, kw in CASES.items():
            agent_id = f"siav-{name}"
            self._seed(agent_id, **kw)
            auth, _ = self._both(agent_id)
            for field, value in auth.items():
                seen.setdefault(field, set()).add(value)

        flat = {"in_turn", "awaiting_input", "worker_present", "env_reachable", "disabled", "alive"}
        constant = sorted(f for f in flat if len(seen.get(f, set())) < 2)
        self.assertEqual(
            constant, [],
            f"these fields never vary across the matrix, so agreement on them proves nothing — add "
            f"a case that flips each: {constant}. Observed: "
            f"{ {f: sorted(map(str, seen.get(f, set()))) for f in flat} }",
        )
