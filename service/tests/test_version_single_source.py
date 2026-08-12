"""The release version is declared ONCE, and drift fails the suite.

Before 2026-08-03 four components each carried their own literal and none tracked a
release: the service reported 0.1.0 (a stale SERVICE_VERSION in .env), config.py's own
default said 4.0.0, api_v2's root endpoint hardcoded 4.0.0, the dashboard hardcoded
0.1.0 — while the project shipped v0.1, v0.1.1 and v0.1.2. Nothing was wired to a
release, so no bump could have propagated.

The one source is the repo-root VERSION file. scripts/stamp.sh bakes it into
service/_build_stamp.json (the container has no repo root — the same reason the sha is
stamped) and config.py reads it from there.
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "service"


def _canonical_version() -> str:
    for line in (REPO_ROOT / "VERSION").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise AssertionError("VERSION contains no usable line")


class VersionSingleSourceTests(unittest.TestCase):
    def test_version_file_is_a_version(self):
        self.assertRegex(_canonical_version(), r"^\d+\.\d+\.\d+([-+].+)?$")

    def test_no_python_source_hardcodes_a_release_version(self):
        # The actual regression guard. A new endpoint or app declaring its own
        # `version="1.2.3"` is invisible in review and is exactly how this drifted.
        literal = re.compile(r'"version"\s*:\s*"\d+\.\d+\.\d+"|version\s*=\s*"\d+\.\d+\.\d+"')
        offenders = []
        for path in [
            SERVICE_DIR / "config.py",
            SERVICE_DIR / "main.py",
            SERVICE_DIR / "new_dashboard_app.py",
            SERVICE_DIR / "control_plane.py",
        ]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if literal.search(line) and "0.0.0-dev" not in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "hardcoded release version(s); read it from get_config().version instead:\n"
            + "\n".join(offenders),
        )

    def test_claude_plugin_manifest_matches_the_version_file(self):
        # A FIFTH place that declared its own version (0.1.0, while the plugin snapshot installed on
        # this host said 3.6.6 and the project shipped v0.1.2). The manifest is user-visible in the
        # plugin listing, so a stale number here misreports the release to anyone installing it.
        import json as _json
        manifest = _json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], _canonical_version())

    def test_stamp_script_writes_the_version_field(self):
        # config.py can only report the real version if stamp.sh actually bakes it in.
        stamp_sh = (REPO_ROOT / "scripts" / "stamp.sh").read_text(encoding="utf-8")
        self.assertIn('"version":"$version"', stamp_sh)
        self.assertIn("VERSION", stamp_sh)

    def test_config_prefers_the_stamped_version_over_its_fallback(self):
        from service.config import ServiceConfig

        self.assertEqual(
            ServiceConfig().version, "0.0.0-dev",
            "the un-stamped fallback must stay obviously unreal — a plausible-looking "
            "default is how 4.0.0 survived three releases unnoticed",
        )

    def test_the_built_stamp_if_present_agrees_with_the_version_file(self):
        # Present in a built checkout; absent in a fresh clone, which is not a failure.
        stamp_path = SERVICE_DIR / "_build_stamp.json"
        if not stamp_path.exists():
            self.skipTest("no _build_stamp.json — run scripts/stamp.sh")
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        if "version" not in stamp:
            self.skipTest("stamp predates the version field")
        self.assertEqual(stamp["version"], _canonical_version())


if __name__ == "__main__":
    unittest.main()


class MisconfiguredStatusTests(unittest.TestCase):
    """`misconfigured` — operator-requested 2026-08-03.

    An identity that can never be started used to report a status that quietly promised
    recovery: a resident with no wake handle read `offline` ("not here right now") and the
    managed fallthrough reads `available` ("send to it and it will cold-start"). Both send the
    operator hunting a delivery bug that does not exist.
    """

    def _inputs(self, **kw):
        from service.status_engine import StatusInputs
        base = dict(mode="managed", alive=False, in_turn=False, awaiting_input=False,
                    worker_present=False, env_reachable=True, disabled=False,
                    bridge_stale=False, has_live_session=False)
        base.update(kw)
        return StatusInputs(**base)

    def test_a_defect_replaces_the_false_available_promise(self):
        from service.status_engine import derive
        self.assertEqual(derive(self._inputs()), "available")
        self.assertEqual(derive(self._inputs(config_defect="runtime 'bogus' cannot be launched")),
                         "misconfigured")

    def test_a_resident_that_cannot_be_woken_is_not_merely_offline(self):
        from service.status_engine import derive
        r = dict(mode="resident", env_reachable=True, bridge_stale=True)
        self.assertEqual(derive(self._inputs(**r)), "offline")
        self.assertEqual(derive(self._inputs(**r, config_defect="no usable wake handle")),
                         "misconfigured")

    def test_a_working_agent_is_never_misconfigured(self):
        # Ranked below every live state on purpose: an agent demonstrably doing work is not
        # broken in any way that matters right now, and flipping a working agent to a red
        # config badge would be worse than the promise this replaces.
        from service.status_engine import derive
        self.assertEqual(
            derive(self._inputs(in_turn=True, worker_present=True, alive=True,
                                config_defect="anything")),
            "working",
        )

    def test_explicit_stop_still_wins(self):
        from service.status_engine import derive
        self.assertEqual(derive(self._inputs(disabled=True, config_defect="anything")), "stopped")

    def test_misconfigured_is_in_the_vocabulary_and_counts_as_NOT_live(self):
        from service.status_engine import VALID_STATUSES
        self.assertIn("misconfigured", VALID_STATUSES)
        js = (REPO_ROOT / "service" / "new_dashboard" / "status.js").read_text(encoding="utf-8")
        self.assertIn("'misconfigured'", js)
        non_live = js.split("NON_LIVE_AGENT_STATUSES = ")[1].split(";")[0]
        self.assertIn("misconfigured", non_live,
                      "must never be counted among agents you can send work to")


class LingeringDeliveredRunTests(unittest.TestCase):
    """A `delivered` run whose reply already landed must be closeable.

    LIVE, found 2026-08-04: seven runs were stuck at status='delivered' with BOTH
    result_message_id and finished_at set — the reply had landed and the run was stamped
    finished, but the status was never flipped. The oldest dated 2026-05-30.

    `_close_reconcilable_delivered_runs` exists to repair exactly that (its own comment calls
    it "class 1: reply landed but the path that linked it didn't close the run"), but its outer
    guard was `COALESCE(finished_at,'') = ''` — and the path that sets result_message_id sets
    finished_at at the same time. So every row in class 1 was filtered out before the class-1
    clause was evaluated. The repair could never see what it was written to repair.
    """

    def test_class_one_is_not_gated_on_an_empty_finished_at(self):
        import re
        src = (REPO_ROOT / "service" / "reconcilers" / "dispatch_queue.py").read_text(encoding="utf-8")
        start = src.index("async def _close_reconcilable_delivered_runs")
        body = src[start:start + 4000]
        where = body[body.index("WHERE status = 'delivered'"):body.index("ORDER BY requested_at ASC")]
        # The result_message_id clause must not sit under an unconditional finished_at guard.
        before_class1 = where[:where.index("COALESCE(result_message_id, '') != ''")]
        self.assertNotIn(
            "COALESCE(finished_at, '') = ''", before_class1,
            "class 1 (reply landed) must be reachable for rows that already have finished_at — "
            "gating it on an empty finished_at is what made 7 runs permanently unreconcilable",
        )
        # …and the age-based classes must KEEP their guard, so this fix does not widen them.
        self.assertGreaterEqual(
            where.count("COALESCE(finished_at, '') = ''"), 2,
            "the stale/orphan classes must still require an empty finished_at",
        )


class ColdStartRefusalReasonTests(unittest.TestCase):
    """N8 — a refusal must name the cause it actually hit.

    `_coldstart_spawn_request_for_dispatch` returns a bare False for FIVE distinct causes and
    every caller rendered ONE sentence for all of them: "No online environment can host managed
    <runtime> for this agent". Reported twice by live agents. On 2026-08-07 that sentence was
    shown while the environment was online with a 9-second-old heartbeat — the real cause was a
    spawn already in flight, and a competent agent was sent to investigate the one thing that
    was fine.
    """

    def test_each_refusal_records_a_distinct_reason(self):
        from service.api_core.dispatch_text import COLDSTART_REFUSED_PREFIX
        from service.api_core.dispatch_start import _coldstart_refusal
        seen = set()
        for reason in ("runtime 'x' is not cold-startable", "this agent is RESIDENT",
                       "a spawn for this agent is ALREADY IN FLIGHT", "no ONLINE environment"):
            w: list[str] = []
            self.assertFalse(_coldstart_refusal(w, reason), "must still return falsey")
            self.assertEqual(len(w), 1)
            self.assertTrue(w[0].startswith(COLDSTART_REFUSED_PREFIX))
            seen.add(w[0])
        self.assertEqual(len(seen), 4, "each cause must produce its OWN text, not a shared one")

    def test_the_message_surfaces_the_reason_not_the_environment_sentence(self):
        from service.api_core.dispatch_text import _coldstart_refusal_message
        from service.api_core.dispatch_start import _coldstart_refusal
        w: list[str] = []
        _coldstart_refusal(w, "a spawn for this agent is ALREADY IN FLIGHT")
        msg = _coldstart_refusal_message(w, "hermes")
        self.assertIn("ALREADY IN FLIGHT", msg)
        self.assertNotIn("No online environment can host", msg,
                         "the whole defect was blaming the environment for every cause")

    def test_no_recorded_reason_degrades_to_a_message_not_to_silence(self):
        from service.api_core.dispatch_text import _coldstart_refusal_message
        for w in (None, [], ["some unrelated advisory"]):
            msg = _coldstart_refusal_message(w, "hermes")
            self.assertIn("hermes", msg)
            self.assertTrue(msg.strip(), "must never be empty")

    def test_the_preexisting_advisory_warning_is_not_mistaken_for_a_reason(self):
        # `warnings` also carries the non-blocking G3 handle-collision advisory. Picking that up
        # as the refusal cause would replace one wrong message with another.
        from service.api_core.dispatch_text import _coldstart_refusal_message
        msg = _coldstart_refusal_message(["bound handle is owned by a different live agent"], "hermes")
        self.assertNotIn("bound handle", msg)

    def test_callers_pass_a_reasons_list_they_actually_declare(self):
        # The first cut of this fix referenced a variable that did not exist in send_message —
        # a NameError on the failure path, i.e. only on the path nobody exercises. Assert every
        # call to the message helper names a list declared in the same function.
        import re
        src = (REPO_ROOT / "service" / "reconcilers" / "dispatch_queue.py").read_text(encoding="utf-8")
        for m in re.finditer(r"_coldstart_refusal_message\((\w+), runtime\)", src):
            var = m.group(1)
            before = src[:m.start()]
            self.assertRegex(
                before, rf"{var}\s*:\s*list\[str\]\s*=\s*\[\]",
                f"{var} is passed but never declared — that is a NameError on the refusal path",
            )
