"""Which of the service's roots a host's aify-env will not start work in, on the environments listing (2026-11-02).

"Edit roots…" edits the service's cwdRoots; aify-env launches only under its own launchRoots and the service can never
widen them. Steven's card listed C:/ and D:/ while every agent under D: read misconfigured. The listing now carries
`rootsOutsideLaunch`, judged by the same rule as the misconfigured note (workspace.py), so the dashboard draws a verdict
instead of a third copy of the containment rule.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase

ENV = "win32:host:default"


class TheEnvironmentsListingSaysWhichRootsAifyEnvWillNotLaunchIn(FastApiTestCase):
    def heartbeat(self, metadata):
        self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENV, "machineId": "win32:host", "os": "win32", "kind": "win32", "runtimes": [],
            "metadata": metadata}).raise_for_status()

    def set_service_roots(self, roots):
        self.client.patch(f"/api/v1/environments/{ENV}/roots",
                          json={"roots": roots, "requestedBy": "dashboard"}).raise_for_status()

    def listed(self):
        answer = self.client.get("/api/v1/environments")
        answer.raise_for_status()
        return next(e for e in answer.json()["environments"] if e["id"] == ENV)

    def test_a_service_root_outside_aify_envs_launch_roots_is_named(self):
        self.heartbeat({"launchRoots": ["C:\\"]})
        self.set_service_roots(["C:/", "D:/", "c:/work"])
        env = self.listed()
        self.assertEqual(env["cwdRoots"], ["C:/", "D:/", "c:/work"], "CONTROL: the service's roots as edited")
        self.assertEqual(env["rootsOutsideLaunch"], ["D:/"],
                         "D:/ is outside C:\\ while C:/ and a case variant beneath it are inside")

    def test_once_aify_env_reports_the_drive_nothing_is_outside(self):
        self.heartbeat({"launchRoots": ["C:\\", "D:/"]})
        self.set_service_roots(["C:/", "D:/Games"])
        self.assertEqual(self.listed()["rootsOutsideLaunch"], [])

    def test_no_reported_launch_roots_is_no_verdict(self):
        """An aify-env that reported no launchRoots says nothing about where it launches; flagging every root would be
        a claim made without evidence."""
        self.heartbeat({})
        self.set_service_roots(["C:/", "D:/"])
        self.assertEqual(self.listed()["rootsOutsideLaunch"], [])
