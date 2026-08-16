"""A restart that cannot cold-start must report WHY, not assert a cause it never checked.

Companion to `test_start_refusal_names_the_real_cause.py`, one layer up. `_prepare_restart_spawn`
handles a session with no stored spawn spec by mirroring the send path: cold-start a managed worker
so a bridge can claim it. `_coldstart_spawn_request_for_dispatch` refuses for five distinct causes
and records which one into the `coldstart_warnings` list — and this caller PASSES that list, so the
reason was being collected all along.

It was then thrown away on the one path that needed it. The success path reports the warnings in the
response body (`session_control.py`), but the refusal raised a hardcoded sentence:

    Session "<id>" has no stored spawn spec and no online environment can host managed <runtime>.

The first clause is the branch condition and is true. The second ASSERTS a cause. It is true only
for the environment-resolution refusals — for a runtime that cannot be cold-started, or a corrupt
environment row, it names a problem nobody checked, and it points the operator at bringing up an
environment that may already be running. That is the N8 shape (operator-reported twice) and the same
defect fixed on the dashboard Start button in `a32efd96`.

Nothing covered this message before this file: it appears in no test in the suite.
"""

import ast
import pathlib
import sqlite3
import unittest

from service.tests._base import FastApiTestCase

REPO = pathlib.Path(__file__).resolve().parents[2]
HELPER = "_coldstart_spawn_request_for_dispatch"

#: Every call site, counted per FILE as (collects the reason, discards it). FROZEN, not ruled: a new
#: call has to be classified here rather than defaulting into either column.
#:
#: The unit is the CALL, not the file, and that distinction is not cosmetic — a first draft of this
#: census classified whole files and immediately failed on `dispatch_launch.py`, which legitimately
#: does both. Counts rather than line numbers so the census survives code moving.
#:
#: Two sites were FIXED after this census found them: `session_ops.py` (a32efd96) and
#: `session_restart.py` (here). The discards below were each examined and left, with the reason.
CENSUS = {
    # ── surface a refusal to a human: must say which of the five causes fired ──
    "service/routers/agents/session_ops.py":  {"collect": 1, "discard": 0},  # Start button -> 409
    "service/api_core/session_restart.py":    {"collect": 1, "discard": 0},  # restart/reset -> 409
    "service/api_core/session_mode_gates.py": {"collect": 1, "discard": 0},  # mode switch -> error
    # Send path (the original N8 fix) reports twice; the third call is a "final safety" cold-start
    # wrapped in `try/except: pass` whose result is never read, so it has no message to improve.
    "service/api_core/dispatch_launch.py":    {"collect": 2, "discard": 1},

    # ── report nothing per call, so there is no message a reason could improve ──
    # Loops over channel members cold-starting whoever is eligible; no per-member outcome.
    "service/api_core/channel_coldstart.py":  {"collect": 0, "discard": 1},
    # Backstop reconciler. EXAMINED AND LEFT: its failure text HEDGES ("up-but-deaf or never
    # started a worker") rather than asserting a cause, so it is not the N8 defect — and the rescue
    # runs for only a subset of runs, so a reason is often absent by construction. Folding it in
    # would be an improvement, not a correction; that is a reviewer's call, not this gate's.
    "service/reconcilers/undeliverable_queued_runs.py": {"collect": 0, "discard": 1},
}
CALLERS_THAT_MUST_COLLECT = {p for p, c in CENSUS.items() if c["collect"]}


def _call_sites() -> dict[str, list[bool]]:
    """Per file, whether each call to the helper passes a `warnings=` keyword."""
    prune = {"node_modules", "tests", "fixtures", "__pycache__", ".git", ".venv"}
    found: dict[str, list[bool]] = {}
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if prune & set(rel.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != HELPER:
                continue
            passes = any(kw.arg == "warnings" for kw in node.keywords)
            found.setdefault(rel.as_posix(), []).append(passes)
    return found


class ColdstartReasonCensusTests(unittest.TestCase):
    """Who calls the helper, and who is allowed to throw the reason away.

    KNOWN LIMIT, stated so this is not read as more than it is: the census sees the CALL, so it
    catches a caller that never collects the reason. It cannot catch one that collects and then
    ignores it — which is exactly what `session_restart.py` did, passing `warnings=` while raising a
    hardcoded sentence, and why these tests pass against that unfixed file. Only the behavioural
    tests above catch that. A call-shape scan proves a reason was ASKED for, never that it was used.
    """

    def test_the_census_still_matches_the_code(self):
        actual = {
            path: {"collect": sum(flags), "discard": len(flags) - sum(flags)}
            for path, flags in _call_sites().items()
        }
        self.assertEqual(
            actual, CENSUS,
            "the cold-start call sites changed. A new call must be classified: does it surface a "
            "refusal to a human? Then it passes `warnings=` and renders with "
            "`_coldstart_refusal_message`, and its `collect` count goes up. If it reports nothing, "
            "raise `discard` and say IN THE CENSUS why there is no message to improve.",
        )

    def test_no_reporting_caller_silently_drops_a_reason(self):
        """The `discard` column is a declaration, not a default. A file that reports refusals AND
        gains a silent call would move counts here, and the census above is what catches it — but
        this asserts the stronger half directly: every file in the must-collect set still collects
        at least as many times as it did when examined."""
        actual = _call_sites()
        for path in CALLERS_THAT_MUST_COLLECT:
            self.assertGreaterEqual(
                sum(actual.get(path, [])), CENSUS[path]["collect"],
                f"{path} lost a `warnings=` argument, so a refusal it surfaces now has to assert a "
                f"cause instead of naming the recorded one — the N8 defect, reported by the "
                f"operator on 2026-07-31 and 2026-08-07",
            )

    def test_the_census_detector_is_not_vacuous(self):
        """A scan that silently matched nothing would pass both tests above."""
        sites = _call_sites()
        self.assertGreaterEqual(sum(len(v) for v in sites.values()), 6, sites)
        self.assertTrue(any(any(flags) for flags in sites.values()), "no call passes warnings at all")


class SessionRestartRefusalNamesTheRealCauseTests(FastApiTestCase):
    DB_NAME = "aify-session-restart-refusal-test.db"

    def _register(self, agent_id: str, **extra):
        payload = {"agentId": agent_id, "role": "coder", "sessionMode": "managed"}
        payload.update(extra)
        r = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

    def _seed_session_without_spawn_spec(self, agent_id: str, *, runtime: str) -> str:
        """A session with a NULL spawn_spec_id — the branch that cold-starts instead of hard-erroring.

        This is the resident-origin shape FIX 5 (2026-06-03) added the cold-start for: such a session
        legitimately has no spec, and a restart must try to create one rather than refusing outright.
        """
        session_id = f"session_{agent_id}"
        env_id = f"env_{agent_id}"
        now = "2099-01-01T00:00:00Z"
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen) "
                "VALUES (?,?,?,?,?)",
                (env_id, "test-host", f"bridge_{agent_id}", now, now),
            )
            conn.execute(
                "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, workspace, "
                "started_at, last_seen, owner_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, agent_id, env_id, runtime, "running", "/workspace", now, now, "managed"),
            )
            conn.commit()
        finally:
            conn.close()
        return session_id

    def _restart(self, session_id: str):
        return self.client.post(
            f"/api/v1/sessions/{session_id}/control",
            json={"action": "restart", "from_agent": "dashboard"},
        )

    def test_a_non_coldstartable_runtime_is_not_reported_as_a_missing_environment(self):
        """The false claim. No environment would ever host this runtime — bringing one up is not
        the fix, and the old message said it was."""
        self._register("srr-badruntime", runtime="notarealruntime")
        session_id = self._seed_session_without_spawn_spec("srr-badruntime", runtime="notarealruntime")

        r = self._restart(session_id)
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json().get("detail", "").lower()

        self.assertIn("no stored spawn spec", detail, "the true half of the message should survive")
        self.assertIn("cold-startable", detail, f"the recorded reason is still being discarded: {detail}")
        self.assertNotIn(
            "no online environment", detail,
            "this refusal has nothing to do with environment availability; the message asserted a "
            "cause the code never checked",
        )

    def test_the_reason_travels_from_the_helper_that_recorded_it(self):
        """Anti-vacuity: proves the text comes from the refusal RECORD, not from a second hardcoded
        sentence that happens to mention the runtime. The reason names the runtime as the helper
        quoted it — repr'd — which no message written at this call site would produce."""
        self._register("srr-quoted", runtime="notarealruntime")
        session_id = self._seed_session_without_spawn_spec("srr-quoted", runtime="notarealruntime")

        detail = self._restart(session_id).json().get("detail", "")
        self.assertIn("'notarealruntime'", detail, detail)
