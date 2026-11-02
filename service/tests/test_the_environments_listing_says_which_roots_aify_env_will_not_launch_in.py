"""Which of the service's roots a host's aify-env will not start work in, on the environments listing (2026-11-02).

"Edit roots…" edits the service's cwdRoots; aify-env launches only under its own launchRoots and the service can never
widen them. Steven's card listed C:/ and D:/ while every agent under D: read misconfigured. The listing now carries
`rootsOutsideLaunch: [{root, add}]`, judged on the same host facts as the misconfigured note (workspace.launch_host) and
quoted by the same command builder (workspace.roots_add_command), so the dashboard draws a verdict instead of copying
either rule.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase

WINDOWS = {"id": "win32:host:default", "machineId": "win32:host", "os": "win32", "kind": "win32"}
LINUX = {"id": "linux:box:default", "machineId": "linux:box", "os": "linux", "kind": "linux"}


class TheEnvironmentsListingSaysWhichRootsAifyEnvWillNotLaunchIn(FastApiTestCase):
    def heartbeat(self, host, metadata):
        self.client.post("/api/v1/environments/heartbeat", json={**host, "runtimes": [], "metadata": metadata}
                         ).raise_for_status()

    def set_service_roots(self, host, roots):
        self.client.patch(f"/api/v1/environments/{host['id']}/roots",
                          json={"roots": roots, "requestedBy": "dashboard"}).raise_for_status()

    def listed(self, host):
        answer = self.client.get("/api/v1/environments")
        answer.raise_for_status()
        return next(e for e in answer.json()["environments"] if e["id"] == host["id"])

    def test_a_service_root_outside_aify_envs_launch_roots_is_named_with_its_command(self):
        self.heartbeat(WINDOWS, {"launchRoots": ["C:\\"]})
        self.set_service_roots(WINDOWS, ["C:/", "D:/", "c:/work"])
        env = self.listed(WINDOWS)
        self.assertEqual(env["cwdRoots"], ["C:/", "D:/", "c:/work"], "CONTROL: the service's roots as edited")
        self.assertEqual(env["rootsOutsideLaunch"], [{"root": "D:/", "add": "aify-env roots add D:/"}],
                         "D:/ is outside C:\\ while C:/ and a case variant beneath it are inside")

    def test_a_spaced_root_gets_one_quoted_argument_and_a_unc_root_gets_no_command(self):
        """Review of 20b7a1de, R2: D:/My Games unquoted is two arguments the host CLI refuses; a UNC folder is one the
        host's parser refuses outright, so no command is offered for it."""
        self.heartbeat(WINDOWS, {"launchRoots": ["C:\\"]})
        self.set_service_roots(WINDOWS, ["D:/My Games", "//server/share"])
        self.assertEqual(self.listed(WINDOWS)["rootsOutsideLaunch"], [
            {"root": "D:/My Games", "add": "aify-env roots add 'D:/My Games'"},
            {"root": "//server/share", "add": ""}])

    def test_a_linux_host_is_judged_as_linux_whatever_its_service_roots_look_like(self):
        """Review of 20b7a1de, R1: the listing passed the service's own roots into the host facts, and a drive-shaped
        entry (D:/stale) flipped a Linux host to case-insensitive matching, so /srv/Repo read as inside /srv/repo while
        the note said outside. Both now judge on the host's reported facts alone."""
        self.heartbeat(LINUX, {"launchRoots": ["/srv/repo"]})
        self.set_service_roots(LINUX, ["D:/stale", "/srv/Repo", "/srv/repo/x"])
        outside = [entry["root"] for entry in self.listed(LINUX)["rootsOutsideLaunch"]]
        self.assertIn("/srv/Repo", outside, "a case variant on Linux is another folder")
        self.assertNotIn("/srv/repo/x", outside, "CONTROL: an ordinary folder inside the launch root")

    def test_once_aify_env_reports_the_drive_nothing_is_outside(self):
        self.heartbeat(WINDOWS, {"launchRoots": ["C:\\", "D:/"]})
        self.set_service_roots(WINDOWS, ["C:/", "D:/Games"])
        self.assertEqual(self.listed(WINDOWS)["rootsOutsideLaunch"], [])

    def test_no_reported_launch_roots_is_no_verdict(self):
        """An aify-env that reported no launchRoots says nothing about where it launches; flagging every root would be
        a claim made without evidence."""
        self.heartbeat(WINDOWS, {})
        self.set_service_roots(WINDOWS, ["C:/", "D:/"])
        self.assertEqual(self.listed(WINDOWS)["rootsOutsideLaunch"], [])
