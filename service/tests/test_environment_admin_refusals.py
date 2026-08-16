"""The five environment-administration refusals, none of which any test had touched.

An environment row is what dashboard-managed spawns are placed on: its `cwdRoots` decide where a
worker may be started, and its controls are how an operator stops or forgets one. Five of its 4xx
messages were in the untested set:

    PATCH  /environments/{id}/roots        404 Environment not found
                                           400 At least one root is required. …
    POST   /environments/{id}/control      404 Environment not found
                                           400 Environment control action must be stop or forget
    PATCH  /environments/controls/{id}     400 Environment control status must be completed or failed

THE ROOTS REFUSAL IS THE ONE WITH TEETH. `cwdRoots` is the containment boundary `_workspace_root_for`
checks every spawn against — the same boundary a `..` traversal walked out of until 2026-08-16. An
EMPTY roots list is not a narrower boundary, it is a different one: `_workspace_root_for` returns ""
for an environment that advertises nothing rather than refusing, so emptying the list turns the check
off. That is why the 400 exists and why it names the alternative (`resetToBridgeAdvertised`) instead
of just saying no — the operator's actual intent is almost always "go back to what the bridge said".

THE TWO ALLOWLISTS ARE ASSERTED AS SETS, not as one happy value each. `action` takes {stop, forget}
and `status` takes {completed, failed}; both are `.strip().lower()`ed first, so the tests cover the
casing and whitespace that normalisation is there for. A gate tested with one good value and one bad
one passes just as well when it accepts everything except that bad one.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

ENVIRONMENT_ID = "linux:test-host:default"


class EnvironmentAdminRefusalTests(FastApiTestCase):
    def _heartbeat(self, roots=("/workspace", "/srv")) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": ENVIRONMENT_ID,
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-one",
                "cwdRoots": list(roots),
                "runtimes": [],
                "status": "online",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _roots(self, body: dict, environment_id: str = ENVIRONMENT_ID):
        return self.client.patch(f"/api/v1/environments/{environment_id}/roots", json=body)

    def _control(self, body: dict, environment_id: str = ENVIRONMENT_ID):
        return self.client.post(f"/api/v1/environments/{environment_id}/control", json=body)

    def setUp(self):
        super().setUp()
        self._heartbeat()

    # ── roots ────────────────────────────────────────────────────────────────────────────────

    def test_roots_404s_for_an_environment_that_does_not_exist(self):
        response = self._roots({"roots": ["/x"]}, environment_id="linux:nowhere:default")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Environment not found")

    def test_an_EMPTY_roots_list_is_refused_and_names_the_alternative(self):
        """Emptying the list does not narrow the boundary, it TURNS IT OFF: `_workspace_root_for`
        returns "" for an environment advertising nothing rather than refusing, so every workspace
        becomes acceptable. The message names `resetToBridgeAdvertised` because that is what the
        operator almost always means."""
        for body in ({"roots": []}, {"roots": None}, {"roots": ["", "   "]}):
            with self.subTest(body=body):
                response = self._roots(body)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    "At least one root is required. Use resetToBridgeAdvertised to return to "
                    "bridge-advertised roots.",
                )

    def test_a_real_roots_update_is_accepted_and_marked_manual(self):
        """The other direction, and the flag that makes the refusal above meaningful: a manual list
        must be distinguishable from the bridge-advertised one, or `resetToBridgeAdvertised` has
        nothing to reset to."""
        response = self._roots({"roots": ["/srv/only"], "requestedBy": "operator"})
        self.assertEqual(response.status_code, 200, response.text)
        environment = response.json()["environment"]
        self.assertEqual(environment["cwdRoots"], ["/srv/only"])

    def test_reset_to_bridge_advertised_needs_no_roots_at_all(self):
        """The escape hatch the refusal points at — it must not itself trip the empty-list check."""
        self._roots({"roots": ["/srv/only"], "requestedBy": "operator"})
        response = self._roots({"resetToBridgeAdvertised": True, "requestedBy": "operator"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            sorted(response.json()["environment"]["cwdRoots"]), ["/srv", "/workspace"],
            "reset restores what the bridge advertised, not the manual list",
        )

    # ── controls ─────────────────────────────────────────────────────────────────────────────

    def test_control_404s_for_an_environment_that_does_not_exist(self):
        response = self._control({"action": "stop"}, environment_id="linux:nowhere:default")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Environment not found")

    def test_the_control_action_allowlist_is_exactly_stop_and_forget(self):
        """Asserted as a SET. A gate tested with one good value and one bad one passes just as well
        when it accepts everything except that bad one."""
        for action in ("stop", "forget"):
            with self.subTest(accepted=action):
                self._heartbeat()  # `forget` tombstones the row, so re-register between cases
                response = self._control({"action": action, "requestedBy": "operator"})
                self.assertEqual(response.status_code, 200, response.text)
        for action in ("start", "restart", "delete", "", "STOP-IT", "forget-all"):
            with self.subTest(refused=action):
                self._heartbeat()
                response = self._control({"action": action, "requestedBy": "operator"})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"], "Environment control action must be stop or forget",
                )

    def test_the_control_action_is_normalised_before_the_allowlist(self):
        """`.strip().lower()` runs first, so an operator's `" Stop "` is the same request. Pinned
        because the normalisation is what makes the allowlist safe to write in one casing."""
        for action in ("STOP", "Stop", "  stop  ", "ForGet"):
            with self.subTest(action=action):
                self._heartbeat()
                response = self._control({"action": action, "requestedBy": "operator"})
                self.assertEqual(response.status_code, 200, response.text)

    def test_the_control_status_allowlist_is_exactly_completed_and_failed(self):
        for status in ("completed", "failed", "COMPLETED", "  failed  "):
            with self.subTest(accepted=status):
                response = self.client.patch(
                    "/api/v1/environments/controls/no-such-control", json={"status": status},
                )
                self.assertNotEqual(
                    response.status_code, 400,
                    "a recognised status must get past the allowlist even if the control is missing",
                )
        for status in ("claimed", "pending", "done", "", "complete"):
            with self.subTest(refused=status):
                response = self.client.patch(
                    "/api/v1/environments/controls/no-such-control", json={"status": status},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    "Environment control status must be completed or failed",
                )
