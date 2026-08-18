"""Settling a dispatch control requires naming who settled it, and the claimer is the only one who may.

THE FINDING (external review, 2026-08-18, reported as a Low): `PATCH /dispatch/controls/{id}` recorded
no acting agent. Anything that could reach the endpoint could mark any control `completed` or `failed`,
and the audit trail said only that it happened.

PROMOTED BY comms-senior-dev, 2026-08-18, verbatim: "Promote only #2: dispatch/controls PATCH receipt
records no acting agent. It is the same accountability/authz class as unsend/artifact/channel owner
fixes. The actor must be mandatory and service-enforced; actor-absent old callers must fail closed
once the deploy-coupled bridge/dashboard/service update is in place."

WHY IT IS NOT MERELY BOOKKEEPING. A control is how a run is interrupted or steered, and settling one
is what closes it. An unattributed settlement means: an interrupt can be marked `completed` by
something that never interrupted anything, and the run proceeds as though the operator's instruction
was carried out. `claim_machine_id` was already recorded at claim time, so the service always had an
owner to check against and simply did not look — the same shape as the unsend and artifact-delete
endpoints before their owner checks landed.

THE MANDATORY ACTOR IS DELIBERATELY FAIL-CLOSED, and that choice has a cost worth stating: this
endpoint's own comment records that "a refused control update leaves the control `pending` forever,
which strands the run it was meant to close". So refusing an actor-less call from a bridge that
predates this change strands runs — which is exactly why the refusal message names the cause and the
fix instead of saying "400". A silent strand is the failure this repo keeps paying for; a strand whose
error says "relaunch this bridge" is a deploy step. `aify-comms doctor`'s `bridge-current` check
already names bridges running older code than the checkout, so the diagnosis path exists.

An OPTIONAL actor would have been security theatre: every old caller keeps working, every new caller
is trusted to opt in, and the audit trail is complete only for callers that chose to be audited.
"""

from __future__ import annotations

import asyncio
import unittest

from service.db import get_db
from service.tests._base import FastApiTestCase

RUN_ID = "run-ctl-actor"
AGENT = "ctl-target"
MACHINE = "win32:test-host"
OTHER_MACHINE = "linux:someone-else"


class DispatchControlSettlementNamesItsActor(FastApiTestCase):
    DB_NAME = "aify-control-actor-test.db"

    def setUp(self):
        super().setUp()
        self._seed_run()

    def _seed_run(self):
        async def run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agents (id, name, role, runtime, session_mode, status,"
                    " registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (AGENT, AGENT, "coder", "claude-code", "managed", "online",
                     "2026-08-18T00:00:00Z", "2026-08-18T00:00:00Z"),
                )
                await db.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at)"
                    " VALUES (?,?,?,?,?)",
                    (RUN_ID, "operator", AGENT, "running", "2026-08-18T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(run())

    def _make_control(self, control_id: str, *, claimed_by: str | None = MACHINE) -> str:
        async def run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO dispatch_controls (id, run_id, from_agent, action, body, status,"
                    " claim_machine_id, requested_at, claimed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (control_id, RUN_ID, "operator", "interrupt", "",
                     "claimed" if claimed_by else "pending", claimed_by or "",
                     "2026-08-18T00:00:00Z", "2026-08-18T00:00:01Z" if claimed_by else None),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(run())
        return control_id

    def _settle(self, control_id: str, **body):
        return self.client.patch(f"/api/v1/dispatch/controls/{control_id}", json=body)

    def _row(self, control_id: str):
        async def run():
            db = await get_db()
            try:
                import aiosqlite
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
                return await cur.fetchone()
            finally:
                await db.close()
        return asyncio.run(run())

    def _events(self, run_id: str = RUN_ID) -> list[str]:
        async def run():
            db = await get_db()
            try:
                cur = await db.execute(
                    "SELECT event_type, body FROM dispatch_events WHERE run_id = ?", (run_id,))
                return [f"{r[0]}|{r[1] or ''}" for r in await cur.fetchall()]
            finally:
                await db.close()
        return asyncio.run(run())

    # ── the actor is mandatory ───────────────────────────────────────────────────────────────

    def test_a_settlement_with_NO_actor_is_refused(self):
        self._make_control("c-noactor")
        response = self._settle("c-noactor", status="completed", response="interrupt accepted")
        self.assertEqual(
            response.status_code, 400,
            "a control settlement with no acting agent was accepted. comms-senior-dev's 2026-08-18 "
            "ruling: the actor must be mandatory and service-enforced, and actor-absent callers fail "
            "closed. An unattributed settlement lets an interrupt be marked completed by something "
            "that never interrupted anything.",
        )
        self.assertEqual(
            self._row("c-noactor")["status"], "claimed",
            "the control was mutated despite the refusal",
        )

    def test_an_EMPTY_actor_is_refused_like_a_missing_one(self):
        """Whitespace is the shape a caller reaches for when a field is mandatory and it has nothing
        to put in it. Accepting it would make the requirement decorative."""
        for blank in ("", "   "):
            with self.subTest(handledBy=repr(blank)):
                control = self._make_control(f"c-blank-{len(blank)}")
                response = self._settle(control, status="completed", handledBy=blank,
                                        machineId=MACHINE)
                self.assertEqual(response.status_code, 400,
                                 f"an actor of {blank!r} was accepted as an identity")

    def test_the_refusal_NAMES_the_cause_and_the_fix(self):
        """A 400 that says only 'bad request' turns a stale bridge into a silently stranded run. This
        endpoint's own comment records that a refused control stays pending forever, so the error text
        is the difference between a deploy step and an outage nobody can explain."""
        self._make_control("c-diag")
        raw = str(self._settle("c-diag", status="completed").json().get("detail", ""))
        # VERBATIM, because `test_every_refusal_is_exercised.py` requires the message's longest static
        # fragment to appear in a test — a refusal nobody quotes is a refusal nobody has read. Asserted
        # against the real response rather than merely written down, so it proves the text as well as
        # satisfying the gate.
        self.assertEqual(
            raw,
            "Control settlement requires an actor: send handledBy=<your agent id> (and machineId). "
            "A bridge running pre-actor code is the likeliest cause — re-run install.sh and RELAUNCH "
            "the wrapper, then retry. Until then this control stays pending and its run will strand.",
            "the actor-refusal message changed; check it still names the cause and the fix",
        )
        detail = raw.lower()
        for expected in ("actor", "relaunch"):
            with self.subTest(expected=expected):
                self.assertIn(
                    expected, detail,
                    f"the refusal message does not mention {expected!r}: {detail!r}. A bridge running "
                    "pre-actor code is the likeliest cause of this 400, and the message is the only "
                    "place the operator will see why their run stranded.",
                )

    # ── only the claimer may settle it ───────────────────────────────────────────────────────

    def test_a_machine_that_did_NOT_claim_the_control_cannot_settle_it(self):
        self._make_control("c-foreign")
        response = self._settle("c-foreign", status="completed", handledBy=AGENT,
                                machineId=OTHER_MACHINE)
        self.assertEqual(
            response.status_code, 409,
            "a machine that never claimed this control was allowed to settle it. claim_machine_id is "
            "recorded at claim time precisely so the settlement can be checked against it.",
        )
        self.assertEqual(self._row("c-foreign")["status"], "claimed",
                         "the foreign settlement mutated the control anyway")
        # The message's longest static fragment, quoted verbatim for the refusal-coverage gate and
        # asserted against the real response so it proves the wording too.
        self.assertIn(
            " cannot settle it. The claiming bridge is the one that ran the control.",
            str(response.json().get("detail", "")),
            "the foreign-settlement refusal no longer explains WHY it was refused; an operator seeing "
            "a bare 409 cannot tell a stale bridge from a bug.",
        )

    def test_the_claiming_machine_CAN_settle_it(self):
        """ANTI-VACUITY: every refusal above would also pass if the endpoint refused everything, which
        would strand every run in the fleet."""
        self._make_control("c-owner")
        response = self._settle("c-owner", status="completed", handledBy=AGENT, machineId=MACHINE,
                                response="interrupt accepted")
        self.assertEqual(response.status_code, 200, response.text)
        row = self._row("c-owner")
        self.assertEqual(row["status"], "completed")

    def test_an_UNCLAIMED_control_may_be_settled_by_a_named_actor(self):
        """The owner check compares against a claim that exists. A control with no recorded claimer has
        no owner to violate, so the actor requirement alone applies — documented here rather than left
        as an accident of the implementation, because it IS a narrower check than the claimed case."""
        self._make_control("c-unclaimed", claimed_by=None)
        response = self._settle("c-unclaimed", status="failed", handledBy=AGENT, machineId=MACHINE,
                                response="no controller")
        self.assertEqual(response.status_code, 200, response.text)

    # ── the actor reaches the record and the audit trail ─────────────────────────────────────

    def test_the_actor_is_STORED_on_the_control(self):
        self._make_control("c-stored")
        self._settle("c-stored", status="completed", handledBy=AGENT, machineId=MACHINE)
        row = self._row("c-stored")
        self.assertEqual(
            (row["handled_by"] if "handled_by" in row.keys() else None), AGENT,
            "the settling actor was accepted but not recorded, so the accountability the ruling asked "
            "for exists only for the duration of the request.",
        )

    def test_the_actor_appears_in_the_run_audit_trail(self):
        self._make_control("c-event")
        self._settle("c-event", status="completed", handledBy=AGENT, machineId=MACHINE)
        joined = " ".join(self._events())
        self.assertIn(
            AGENT, joined,
            "the dispatch event for this settlement does not name who settled it. The run audit trail "
            f"is where a stranded or wrongly-closed run is investigated. Events: {joined!r}",
        )


if __name__ == "__main__":
    unittest.main()
