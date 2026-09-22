"""The two endpoints that DELETE things, neither of which any test had called.

`POST /rotate` enforces retention — expiring messages past the window and trimming per-agent
inboxes — and `DELETE /shared/{name}` removes a shared artifact, including its file on disk. Both
handlers were among the 71 service functions the suite never entered.

DELETION IS THE ONE CLASS OF BUG THAT CANNOT BE FIXED AFTER THE FACT. A message that should have
been kept is gone; a file unlinked is gone. So the tests here are weighted towards what must NOT be
deleted: a message inside the retention window, an inbox under its cap, the OTHER agent's messages
when one agent is over, and every artifact whose name was not the one asked for.

TRIMMING KEEPS THE NEWEST, and that direction is the whole value of the feature — an inbox trimmed
from the wrong end leaves an agent holding only history it has already dealt with. It is asserted by
content, not by count, because a count is satisfied either way.

Both limits are OFF at 0, the default, and then rotation answers `{"ok": false}` rather than raising:
an operator who has not asked for deletion gets a clear no-op, not an error to investigate. Since
2026-09-19 the sweep also runs rotation hourly; the sweep half is at the end of this file.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "lc-keeper"
OTHER = "lc-other"
DAY_MS = 86_400_000


class RetentionAndDeletionTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, OTHER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ── helpers ──────────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(r) for r in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_message(self, message_id: str, *, to_agent: str = AGENT, age_days: float = 0.0,
                      subject: str = "") -> None:
        timestamp = int(time.time() * 1000) - int(age_days * DAY_MS)
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, subject, body, type, priority,"
            " timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (message_id, OTHER if to_agent == AGENT else AGENT, to_agent,
             subject or message_id, "body", "info", "normal", timestamp),
        )

    def _settings(self, **values) -> None:
        response = self.client.put("/api/v1/settings", json=values)
        self.assertEqual(response.status_code, 200, response.text)

    def _rotate(self):
        return self.client.post("/api/v1/rotate")

    def _message_ids(self, to_agent: str = AGENT):
        return [
            r["id"] for r in self._rows(
                "SELECT id FROM messages WHERE to_agent = ? ORDER BY timestamp", (to_agent,),
            )
        ]

    # ── rotation: what it must NOT delete ────────────────────────────────────────────────────

    def _seed_run(self, run_id: str, message_id: str, status: str) -> None:
        """A dispatch run pointing at a message, in whatever state the case needs."""
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, requested_at)"
            " VALUES (?,?,?,?,?,?)",
            (run_id, message_id, OTHER, AGENT, status, "2026-01-01T00:00:00Z"),
        )

    def test_a_message_an_OPEN_run_is_waiting_on_is_never_expired(self):
        """EXTERNAL REVIEW, 2026-09-21, finding 4.

        Rotation filtered on age and recipient only. Deleting a message NULLs
        `dispatch_runs.message_id`, and the closing path matches a reply on
        `WHERE target_agent = ? AND message_id = ?` -- so rotation could delete the message a live
        run was waiting on, leaving it open and overdue with no id left to quote and nothing able to
        close it. Age is not evidence that work has finished.
        """
        self._seed_message("m-awaited", age_days=400)
        self._seed_run("run-open", "m-awaited", "running")
        self._settings(message_retention_days=30)
        self._rotate()
        self.assertIn("m-awaited", self._message_ids(), "an open run's message must survive rotation")

    def test_CONTROL_the_same_old_message_goes_once_its_run_has_ENDED(self):
        """In the same run, or the guard above could be refusing to delete anything at all."""
        self._seed_message("m-done", age_days=400)
        self._seed_run("run-done", "m-done", "completed")
        self._settings(message_retention_days=30)
        self._rotate()
        self.assertNotIn("m-done", self._message_ids(), "a finished run must not pin history forever")

    def test_CONTROL_an_old_message_no_run_points_at_is_still_expired(self):
        self._seed_message("m-orphan", age_days=400)
        self._settings(message_retention_days=30)
        self._rotate()
        self.assertNotIn("m-orphan", self._message_ids())

    def test_the_CAP_also_spares_a_message_an_open_run_is_waiting_on(self):
        """The other half: the cap trims the OLDEST, which is exactly where an awaited message sits."""
        for i in range(5):
            self._seed_message(f"m-cap-{i}", age_days=10 - i)
        self._seed_run("run-cap", "m-cap-0", "queued")
        self._settings(message_cap_per_agent=2)
        self._rotate()
        surviving = self._message_ids()
        self.assertIn("m-cap-0", surviving, "the cap must not evict an open run's message either")
        self.assertNotIn("m-cap-1", surviving, "...while still trimming the rest, or it trimmed nothing")


    def test_a_message_past_the_window_is_expired(self):
        """And one inside it survives. This is also the gate on the cutoff's UNITS: an ISO-string
        cutoff deletes both rows (every INTEGER sorts below every TEXT), a seconds cutoff deletes
        neither."""
        self._settings(message_retention_days=30, message_cap_per_agent=0)
        self._seed_message("ancient", age_days=45)
        self._seed_message("recent", age_days=1)
        response = self._rotate()
        self.assertEqual(response.status_code, 200, response.text)
        stats = response.json()["stats"]
        self.assertEqual(self._message_ids(), ["recent"])
        self.assertEqual(stats["expired_messages"], 1, "the report must match what was deleted")

    def test_the_window_follows_the_SETTING_not_a_hardcoded_default(self):
        """An operator lowering retention expects it to take effect; one raising it expects their
        history back under protection. A hardcoded window would silently ignore both."""
        self._settings(message_retention_days=7, message_cap_per_agent=0)
        self._seed_message("ten-days-old", age_days=10)
        self._rotate()
        self.assertEqual(self._message_ids(), [])

    def test_rotation_with_both_limits_at_zero_deletes_NOTHING_and_says_why(self):
        self._settings(message_retention_days=0, message_cap_per_agent=0)
        self._seed_message("ancient", age_days=99)
        response = self._rotate()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["ok"], False)
        self.assertIn("off", response.json()["reason"].lower())
        self.assertEqual(self._message_ids(), ["ancient"], "rotation ran while switched off")

    # ── rotation: trimming keeps the NEWEST ──────────────────────────────────────────────────

    def test_an_inbox_over_its_cap_is_trimmed_from_the_OLDEST_end(self):
        """The direction is the whole feature. Trimming the newest would leave an agent holding
        only history it has already dealt with."""
        self._settings(message_retention_days=0, message_cap_per_agent=2)
        for index, age in enumerate([5, 3, 1]):
            self._seed_message(f"m{index}", age_days=age)
        stats = self._rotate().json()["stats"]
        self.assertEqual(self._message_ids(), ["m1", "m2"], "the wrong end of the inbox was trimmed")
        self.assertEqual(stats["trimmed_messages"], 1)

    def test_an_inbox_under_its_cap_is_left_alone(self):
        self._settings(message_retention_days=0, message_cap_per_agent=10)
        for index in range(3):
            self._seed_message(f"m{index}", age_days=index)
        self._rotate()
        self.assertEqual(len(self._message_ids()), 3)

    def test_one_agent_being_over_does_not_trim_ANOTHER_agents_inbox(self):
        """The cap is per agent. A shared count would delete a quiet agent's history because a busy
        one was over.

        THE QUIET AGENT NEEDS TWO MESSAGES, and that is not padding. The count is re-read inside the
        per-agent loop, so with a global count the FIRST agent's trim can drop the total back under
        the cap and the second agent is spared by accident — my first fixture gave the quiet agent
        one message and the mutation survived. Two means the shared count is still over when its
        turn comes.
        """
        self._settings(message_retention_days=0, message_cap_per_agent=2)
        for index, age in enumerate([9, 7, 5, 3]):
            self._seed_message(f"busy{index}", to_agent=AGENT, age_days=age)
        for index, age in enumerate([8, 6]):
            self._seed_message(f"quiet{index}", to_agent=OTHER, age_days=age)
        self._rotate()
        self.assertEqual(self._message_ids(OTHER), ["quiet0", "quiet1"],
                         "another agent's inbox was trimmed because THIS one was over")
        self.assertEqual(self._message_ids(AGENT), ["busy2", "busy3"])

    def test_rotation_takes_a_messages_receipts_WITH_it(self):
        """An orphaned receipt is a claim about a message that no longer exists; left behind it
        accumulates forever and can resurrect as a false 'already read'.

        RENAMED 2026-09-22, because the old name described a sweep this no longer does and never
        needed to. `_delete_messages_by_ids` deletes each message's receipts BY ID in the same
        transaction, so the receipt goes with the message. The full-table `NOT IN` that used to run
        after rotation was re-answering that, and this test was green because of the by-id delete
        rather than because of the sweep it was named for (review finding 12).
        """
        self._settings(message_retention_days=1, message_cap_per_agent=0)
        self._seed_message("ancient", age_days=99)
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            ("ancient", AGENT, "2026-08-16T00:00:00Z"),
        )
        self._rotate()
        self.assertEqual(self._rows("SELECT * FROM read_receipts"), [])

    def test_a_receipt_for_a_LIVE_message_is_not_cleared(self):
        self._settings(message_retention_days=30, message_cap_per_agent=0)
        self._seed_message("kept", age_days=1)
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            ("kept", AGENT, "2026-08-16T00:00:00Z"),
        )
        self._rotate()
        self.assertEqual(len(self._rows("SELECT * FROM read_receipts")), 1)

    # ── rotation from the sweep ──────────────────────────────────────────────────────────────

    def _sweep(self, *, due: bool = True) -> dict:
        from service.reconcilers import message_rotation
        from service.reconcilers.sweep import _run_dispatch_reconcile_once

        schedule = message_rotation.RotationSchedule()
        if not due:
            schedule.mark()
        message_rotation.SWEEP_SCHEDULE = schedule
        return asyncio.run(_run_dispatch_reconcile_once())

    def test_the_sweep_rotates_when_a_limit_is_set_and_reports_it(self):
        self._settings(message_retention_days=30, message_cap_per_agent=0)
        self._seed_message("ancient", age_days=45)
        self._seed_message("recent", age_days=1)
        report = self._sweep()
        self.assertEqual(self._message_ids(), ["recent"])
        self.assertEqual(report["messages_expired"], 1)

    def test_the_sweep_deletes_nothing_at_the_defaults(self):
        """The defaults are 0 and 0. A host that never touched these settings keeps every message."""
        self._seed_message("ancient", age_days=4000)
        report = self._sweep()
        self.assertEqual(self._message_ids(), ["ancient"])
        self.assertEqual(report["messages_expired"], 0)

    def test_the_sweep_ignores_the_retired_keys_that_never_took_effect(self):
        """Hosts whose settings page was saved hold `retention_days` 90 and `max_messages_per_agent`
        1000 from when nothing ran rotation. Scheduling it must not start honouring them."""
        self._write("INSERT OR REPLACE INTO settings (key, value) VALUES ('retention_days', '1')")
        self._write("INSERT OR REPLACE INTO settings (key, value) VALUES ('max_messages_per_agent', '1')")
        from service.api_core.settings import _invalidate_settings_cache
        _invalidate_settings_cache()
        self._seed_message("ancient", age_days=45)
        self._seed_message("recent", age_days=1)
        self._sweep()
        self.assertEqual(self._message_ids(), ["ancient", "recent"])

    def test_the_sweep_rotates_at_most_once_an_hour(self):
        self._settings(message_retention_days=30, message_cap_per_agent=0)
        self._seed_message("ancient", age_days=45)
        self._sweep(due=False)
        self.assertEqual(self._message_ids(), ["ancient"], "rotation ran before its hour was up")

    def test_the_schedule_is_due_first_then_after_its_interval(self):
        from service.reconcilers.message_rotation import RotationSchedule

        now = [1000.0]
        schedule = RotationSchedule(interval_seconds=3600, clock=lambda: now[0])
        self.assertTrue(schedule.due())
        schedule.mark()
        now[0] += 3599
        self.assertFalse(schedule.due())
        now[0] += 1
        self.assertTrue(schedule.due())

    # ── deleting a shared artifact ───────────────────────────────────────────────────────────

    def _share(self, name: str, content: str = "hello") -> None:
        # FORM fields, not JSON: the share endpoint takes multipart because the same route accepts a
        # file upload. Posting JSON 422s on every field, which is what my first version did.
        response = self.client.post(
            "/api/v1/shared",
            data={"name": name, "from_agent": AGENT, "content": content, "description": "d"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_deleting_an_artifact_removes_exactly_that_one(self):
        self._share("keep-me.md")
        self._share("delete-me.md")
        response = self.client.delete(f"/api/v1/shared/delete-me.md?requestedBy={AGENT}")
        self.assertEqual(response.status_code, 200, response.text)
        names = [r["name"] for r in self._rows("SELECT name FROM shared_artifacts")]
        self.assertEqual(names, ["keep-me.md"])

    def test_deleting_an_artifact_that_does_not_exist_is_not_an_error(self):
        """It is idempotent by design — a retrying caller must not be told something went wrong —
        and the assertion below is what stops that reading as "delete succeeds silently on a typo"
        for a name that DOES exist."""
        self._share("keep-me.md")
        response = self.client.delete(f"/api/v1/shared/never-existed.md?requestedBy={AGENT}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self._rows("SELECT name FROM shared_artifacts")), 1)

    def test_an_invalid_artifact_name_is_refused_before_anything_is_deleted(self):
        """`validate_name` runs first, so a name that is not a name never reaches a DELETE or a
        file unlink.

        A TRAVERSAL SHAPE IS THE WRONG FIXTURE HERE: `..%2Fescape` and `../escape` are 404s from the
        ROUTER — a path segment cannot contain a slash — so they never reach the handler and prove
        nothing about this guard. My first version used one and the mutation removing
        `validate_name` survived. A name with a space does reach it.
        """
        self._share("keep-me.md")
        response = self.client.delete(f"/api/v1/shared/bad name?requestedBy={AGENT}")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Invalid artifact name", response.json()["detail"])
        self.assertEqual(len(self._rows("SELECT name FROM shared_artifacts")), 1)

    def test_a_traversal_shaped_name_never_reaches_the_handler_at_all(self):
        """Recorded separately, because it is a different layer doing the work: the router refuses
        it, and that is worth knowing when reading the guard above."""
        self._share("keep-me.md")
        for path in ("/api/v1/shared/..%2Fescape", "/api/v1/shared/../escape"):
            with self.subTest(path=path):
                self.assertEqual(self.client.delete(path).status_code, 404)
        self.assertEqual(len(self._rows("SELECT name FROM shared_artifacts")), 1)

    def test_deleting_a_BINARY_artifact_unlinks_its_file_too(self):
        """The row and the file are two halves of one artifact. Leaving the file behind fills the
        data volume with blobs nothing references and nothing will ever clean up."""
        blob = Path(self._tmpdir.name) / "blob.bin"
        blob.write_bytes(b"\x00binary")
        self._write(
            "INSERT INTO shared_artifacts (name, from_agent, description, content, size,"
            " is_binary, file_path, shared_at) VALUES (?,?,?,?,?,?,?,?)",
            ("blob.bin", AGENT, "d", "", 7, 1, str(blob), "2026-08-16T00:00:00Z"),
        )
        self.assertTrue(blob.exists())
        self.assertEqual(self.client.delete(f"/api/v1/shared/blob.bin?requestedBy={AGENT}").status_code, 200)
        self.assertFalse(blob.exists(), "the artifact row went but its file stayed")
        self.assertEqual(self._rows("SELECT name FROM shared_artifacts"), [])

    def test_a_binary_artifact_whose_file_is_ALREADY_gone_still_deletes_the_row(self):
        """The file can be removed out from under the row by an operator or a volume reset. Raising
        here would leave the row undeletable forever."""
        missing = Path(self._tmpdir.name) / "not-there.bin"
        self._write(
            "INSERT INTO shared_artifacts (name, from_agent, description, content, size,"
            " is_binary, file_path, shared_at) VALUES (?,?,?,?,?,?,?,?)",
            ("not-there.bin", AGENT, "d", "", 7, 1, str(missing), "2026-08-16T00:00:00Z"),
        )
        self.assertEqual(self.client.delete(f"/api/v1/shared/not-there.bin?requestedBy={AGENT}").status_code, 200)
        self.assertEqual(self._rows("SELECT name FROM shared_artifacts"), [])
