r"""aify-env describes the host, the bridge describes itself, and neither erases the other.

THE CUTOVER, tested where it actually lands. Since 2026-08-30 exactly one tier advertises a host's
runtimes: aify-env does it, and the aify-comms bridge omits `runtimes`, `terminal`, `pty` and
`terminalRuntimes` whenever aify-env's `/health` reports `advertising: true`. Two writers on
last-writer-wins fields made the row change on every beat, which reads like failing hardware rather
than like two components disagreeing.

WHAT MAKES THE SPLIT SURVIVABLE is the preservation rule one module over: a heartbeat that omits a
field no longer blanks it. Before that, a bridge standing down would have EMPTIED the runtimes rather
than leaving them to aify-env, and the environment would have advertised nothing spawnable. These
tests interleave the two beats in both orders because that is the property, not the implementation:
whoever beats second must not undo the first.

THE THREE FIELDS EACH TIER KEEPS TO ITSELF are asserted too, since they are the ones with no second
writer at all -- if the tier that owns one stops sending it, nothing else will.
"""

from __future__ import annotations

from service.tests._base import FastApiTestCase

ENV_ID = "windows:two-tiers:default"

#: What aify-env sends: host facts, no id, no label, no cwdRoots.
ENVIRONMENT_BEAT = {
    "kind": "windows",
    "hostname": "two-tiers",
    "os": "windows",
    "machineId": "win32:two-tiers",
    "runtimes": [
        {"runtime": "claude", "available": True, "unavailableReason": ""},
        {"runtime": "hermes", "available": True, "unavailableReason": ""},
    ],
    "terminalRuntimes": ["claude", "hermes"],
    "terminal": True,
    "pty": True,
    "metadata": {"advertiser": "aify-env"},
}

#: What the bridge sends once it has stood down: its own identity, plus the fields aify-env never
#: sends. No runtimes, no terminal, no pty, no terminalRuntimes.
BRIDGE_BEAT_STOOD_DOWN = {
    "id": ENV_ID,
    "label": "Windows on two-tiers",
    "machineId": "win32:two-tiers",
    "os": "windows",
    "kind": "windows",
    "bridgeId": "bridge-A",
    "bridgeVersion": "0.6.0",
    "launcherVersion": "0.6.0",
    "cwdRoots": ["C:/Docker"],
    "metadata": {"bridgeStartedAt": "2026-08-30T10:00:00Z"},
}


class TwoTiersDescribeOneEnvironmentTests(FastApiTestCase):
    def _beat(self, body: dict) -> dict:
        response = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _runtimes(self, environment) -> list[str]:
        return sorted(r.get("runtime", "") for r in environment.get("runtimes") or [])

    def test_the_bridge_beating_after_aify_env_keeps_the_runtimes(self):
        """The cutover's whole premise. If this fails, standing down empties the row and every managed
        agent on the host becomes unspawnable."""
        self._beat(ENVIRONMENT_BEAT)
        after = self._beat(BRIDGE_BEAT_STOOD_DOWN)
        self.assertEqual(["claude-code", "hermes"], self._runtimes(after),
                         "the bridge's beat erased the runtimes aify-env advertised")
        self.assertTrue(after["terminal"], "the terminal answer was lost")
        # CANONICAL on both lists. `terminalRuntimes` was already normalised on write and `runtimes`
        # was not, so the two disagreed about the same host: one said `claude-code`, the other
        # `claude`. Both readers normalise before comparing, so nothing was broken -- but a row whose
        # two runtime lists spell the same runtime differently is a row nobody can read at a glance.
        self.assertEqual(["claude-code", "hermes"], sorted(after["terminalRuntimes"]))
        self.assertEqual(self._runtimes(after), sorted(after["terminalRuntimes"]),
                         "the two runtime lists on one row disagree about the same host")

    def test_aify_env_beating_after_the_bridge_keeps_the_bridge_identity(self):
        """The other order, and the other direction. Supersession is arbitrated on `bridgeId` and
        `bridgeStartedAt`; aify-env has neither, and erasing them disarms it silently."""
        self._beat(BRIDGE_BEAT_STOOD_DOWN)
        after = self._beat(ENVIRONMENT_BEAT)
        self.assertEqual("bridge-A", after["bridgeId"], "the advertisement erased the bridge id")
        self.assertEqual("0.6.0", after["bridgeVersion"])
        self.assertEqual("0.6.0", after["launcherVersion"])
        self.assertEqual("2026-08-30T10:00:00Z", after.get("metadata", {}).get("bridgeStartedAt"))

    def test_the_operators_label_and_roots_survive_the_advertisement(self):
        """aify-env sends neither, precisely so these survive. If it ever starts sending them, this
        goes red rather than the operator noticing their machine renamed itself."""
        self._beat(BRIDGE_BEAT_STOOD_DOWN)
        after = self._beat(ENVIRONMENT_BEAT)
        self.assertEqual("Windows on two-tiers", after["label"],
                         "the advertisement renamed the environment")
        self.assertEqual(["C:/Docker"], after["cwdRoots"],
                         "the advertisement erased the configured work roots")

    def test_repeated_alternating_beats_do_not_flap(self):
        """THE FAILURE THIS CUTOVER EXISTS TO PREVENT, driven rather than argued. Ten alternating
        beats must leave one stable row; two advertisers would make it change on every one."""
        self._beat(ENVIRONMENT_BEAT)
        self._beat(BRIDGE_BEAT_STOOD_DOWN)
        settled = self._beat(ENVIRONMENT_BEAT)
        watched = ("runtimes", "terminalRuntimes", "terminal", "pty", "label", "cwdRoots",
                   "bridgeId", "machineId", "os", "kind")
        expected = {key: settled.get(key) for key in watched}
        for round_number in range(5):
            for beat in (BRIDGE_BEAT_STOOD_DOWN, ENVIRONMENT_BEAT):
                seen = self._beat(beat)
                for key in watched:
                    self.assertEqual(expected[key], seen.get(key),
                                     f"{key} changed on round {round_number}: the row is flapping")

    def test_a_tier_that_SPEAKS_still_overrides_the_stored_answer(self):
        """The other half of preservation, and the half a first gate missed. Two mutations survived
        until this existed: one that let the stored value win over an explicit claim, and one that
        preserved even when the caller HAD spoken. Both would freeze the terminal answer -- a host
        whose PTY support broke would advertise `terminal: true` for ever."""
        self._beat(ENVIRONMENT_BEAT)
        broken = {**ENVIRONMENT_BEAT, "terminal": False, "pty": False, "terminalRuntimes": []}
        after = self._beat(broken)
        self.assertFalse(after["terminal"], "a host reporting a lost terminal was not believed")
        self.assertFalse(after["pty"])
        self.assertEqual([], after["terminalRuntimes"],
                         "an explicit empty runtime list was overridden by the stored one")

    def test_a_changed_terminal_runtime_list_replaces_rather_than_merges(self):
        self._beat(ENVIRONMENT_BEAT)
        after = self._beat({**ENVIRONMENT_BEAT, "terminalRuntimes": ["codex"]})
        self.assertEqual(["codex"], after["terminalRuntimes"])

    def test_an_advertisement_keeps_every_bridge_metadata_key(self):
        """THE REGRESSION THIS CUTOVER CAUSED, and the reason it was findable at all.

        `bridgeBuild` is how `bridge-current` answers "is a running bridge executing old code" -- the
        question `bridge-installed` cannot, because a process keeps what it loaded at boot. It rides
        in `metadata`, `next_metadata` replaces the blob, and aify-env beats every 30s: a bridge's
        reported build was erased within half a minute of being written. Measured on the deployed
        system -- a freshly started bridge on current code, and doctor still saying no bridge reports
        its build.

        The preserved set is now derived from the `bridge` prefix, so a key added to the bridge's
        payload later survives without anyone remembering. This drives ALL of them, including one
        invented here, because a test naming only today's three would not have caught tomorrow's."""
        bridge = {**BRIDGE_BEAT_STOOD_DOWN, "metadata": {
            "bridgeStartedAt": "2026-08-30T10:00:00Z",
            "bridgeBuild": "6035d5a3",
            "bridgeLastSeen": "2026-08-30T10:00:05Z",
            "bridgeSomethingAddedLater": "future",
            "pid": 1234,
        }}
        self._beat(bridge)
        after = self._beat(ENVIRONMENT_BEAT)
        metadata = after.get("metadata") or {}
        for key in ("bridgeStartedAt", "bridgeBuild", "bridgeLastSeen", "bridgeSomethingAddedLater"):
            self.assertIn(key, metadata, f"the advertisement erased {key}, which only a bridge can send")
        self.assertEqual("6035d5a3", metadata["bridgeBuild"],
                         "bridge-current is blind again: the build a bridge reported was replaced")

    def test_a_bridge_that_did_NOT_stand_down_still_works(self):
        """A host without aify-env, or with an older one that cannot report `advertising`. The bridge
        keeps sending host facts and they are believed -- standing down is the exception."""
        full = {**BRIDGE_BEAT_STOOD_DOWN,
                "runtimes": [{"runtime": "codex", "available": True, "unavailableReason": ""}],
                "terminalRuntimes": ["codex"], "terminal": True, "pty": True}
        after = self._beat(full)
        self.assertEqual(["codex"], self._runtimes(after))
        self.assertEqual(["codex"], sorted(after["terminalRuntimes"]))
