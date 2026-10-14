"""The exact SQL two handlers issue, frozen before their bodies leave the router (audit item S2).

WHAT THIS FREEZES. `POST /environments/heartbeat` and `GET /analytics` held their behaviour in the
router. Moving it into `service/api_core/` must not change one statement, its parameters, its order,
the connection it runs on, or where the transaction begins and ends. A response-shape test cannot see
any of that: the heartbeat's `BEGIN IMMEDIATE` has to precede the host-proof read, and its COMMIT has
to precede the terminal cleanup and the websocket broadcast, and every one of those orders returns the
same body.

RECORDED FROM COMMIT 2223c45b, BEFORE EXTRACTION. Every expected list below was generated once by
running these scenarios against that commit's handlers and pasting the result in. It is a
characterization, not a specification: a mismatch means the move changed behaviour, and if a change is
ever intended, the list is the record of what was chosen.

ONE INTENDED CHANGE SINCE, before the extraction: main dropped the analytics message-total query (the
tile is now the sum of the series it is shown with, review of 0.9.5, L7), so that one entry is gone from
both analytics lists. Every other entry and both response bodies are as recorded.

THE INSTRUMENT is below the handler, not beside it: every `aiosqlite.Connection` statement, commit,
rollback and close is logged in call order, tagged with the connection it ran on (`c1` is the first
connection the request touched). It does not care which module issues the SQL, so it reads the same
before and after the extraction. Websocket broadcasts are logged into the same sequence, which is what
pins "commit, then broadcast".

WHAT IS NORMALISED, and only this: whitespace in SQL collapses to one space; an ISO timestamp anywhere
in a parameter or a response string reads `<iso>`; a UUID reads `<uuid>`; an integer of 10**9 or more
(an epoch in seconds or milliseconds) reads `<epoch>`. Everything else, including JSON blobs written
as parameters (and therefore their key order), is compared verbatim. Runs of an identical entry are
run-length encoded as `(count, entry)` -- lossless, and it keeps the analytics buckets (24 + 30 + 12
identical COUNT statements) readable.

DETERMINISM. The live-status cache is cleared before each recorded request, and the settings cache
(a 5 s monotonic TTL, bypassed whenever pytest is loaded) gets a TTL of zero while it runs, so every
`_load_settings` is a query under pytest and under unittest alike and a slow machine cannot change
which reads hit the cache.

`test_the_recorder_records` and `test_the_recorder_can_say_no` are the positive and negative controls:
a recorder that saw nothing, or that compared nothing, would pass every assertion here.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any
from unittest import mock

import aiosqlite

from service.tests._base import FastApiTestCase

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}")
_RECORDED_METHODS = (
    "execute", "executemany", "execute_fetchall", "execute_insert", "executescript",
    "commit", "rollback", "close",
)


def _norm_text(value: str) -> str:
    return _UUID.sub("<uuid>", _ISO.sub("<iso>", value))


def _norm_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and abs(value) >= 10 ** 9:
        return "<epoch>"
    if isinstance(value, str):
        return _norm_text(value)
    if isinstance(value, dict):
        return {key: _norm_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm_value(item) for item in value]
    return value


def _rle(entries: list[str]) -> list:
    encoded: list = []
    for entry in entries:
        if encoded:
            last = encoded[-1]
            last_entry = last[1] if isinstance(last, tuple) else last
            if last_entry == entry:
                encoded[-1] = ((last[0] if isinstance(last, tuple) else 1) + 1, entry)
                continue
        encoded.append(entry)
    return encoded


class SqlRecorder:
    """Logs every statement and transaction call on any aiosqlite connection, in call order."""

    def __init__(self) -> None:
        self.entries: list[str] = []
        self._connections: list = []

    def _tag(self, connection) -> str:
        for index, seen in enumerate(self._connections):
            if seen is connection:
                return f"c{index + 1}"
        self._connections.append(connection)
        return f"c{len(self._connections)}"

    def log(self, connection, verb: str, sql: Any = None, parameters: Any = None) -> None:
        entry = f"{self._tag(connection)} {verb}"
        if sql is not None:
            entry += " " + " ".join(str(sql).split())
        if parameters:
            entry += " | " + json.dumps(_norm_value(list(parameters)), sort_keys=False)
        self.entries.append(entry)

    def patches(self) -> list:
        recorder = self
        patched = []
        for name in _RECORDED_METHODS:
            original = getattr(aiosqlite.Connection, name)

            def wrapper(connection, *args, _method=name, _original=original, **kwargs):
                if _method in ("commit", "rollback", "close"):
                    recorder.log(connection, _method.upper())
                elif _method == "executescript":
                    recorder.log(connection, "SCRIPT", args[0] if args else kwargs.get("sql_script"))
                else:
                    sql = args[0] if args else kwargs.get("sql")
                    params = args[1] if len(args) > 1 else kwargs.get("parameters")
                    recorder.log(connection, "SQL" if _method == "execute" else _method.upper(), sql, params)
                return _original(connection, *args, **kwargs)

            patched.append(mock.patch.object(aiosqlite.Connection, name, wrapper))
        return patched


class RecordingWS:
    def __init__(self, recorder: SqlRecorder) -> None:
        self._recorder = recorder

    async def broadcast(self, event, payload=None, *args, **kwargs):
        self._recorder.entries.append(f"WS {event} {json.dumps(_norm_value(payload))}")

    async def notify_agent(self, *args, **kwargs):
        self._recorder.entries.append("WS notify_agent")


def _summarise_analytics(body: dict) -> dict:
    """Bucket labels and dates move with the wall clock; their count, and what they add up to, do not."""
    summary = dict(body)
    for key in ("messagesPerHour", "messagesPerDay", "messagesPerMonth", "messagesPerAllTime"):
        series = body[key]
        summary[key] = {"len": len(series), "total": sum(point["count"] for point in series)}
    outcomes = body["dispatchOutcomes"]
    summary["dispatchOutcomes"] = {
        "len": len(outcomes),
        "completed": sum(point["completed"] for point in outcomes),
        "failed": sum(point["failed"] for point in outcomes),
    }
    return _norm_value(summary)


ENV = "windows:freeze-host:default"
MACHINE = "win32:freeze-host"


class HeartbeatAndAnalyticsSqlFreezeTests(FastApiTestCase):
    DB_NAME = "aify-test-sql-freeze.db"

    # ------------------------------------------------------------------ recording

    def _record(self, method: str, url: str, **kwargs) -> tuple[int, Any, list]:
        from service.api_core import settings as settings_module
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE

        recorder = SqlRecorder()
        settings_module._invalidate_settings_cache()
        _LIVE_STATE_CACHE.clear()
        self._app.state.ws_manager = RecordingWS(recorder)
        patches = recorder.patches() + [mock.patch.object(settings_module, "_SETTINGS_CACHE_TTL", 0.0)]
        for patch in patches:
            patch.start()
        try:
            response = getattr(self.client, method)(url, **kwargs)
        finally:
            for patch in reversed(patches):
                patch.stop()
            self._app.state.ws_manager = self.ws
        return response.status_code, response.json(), _rle(recorder.entries)

    def _beat(self, headers=None, **overrides) -> Any:
        body = {
            "id": ENV, "kind": "windows", "os": "windows", "hostname": "freeze-host",
            "machineId": MACHINE, "label": "Freeze host",
            "runtimes": [{"runtime": "claude", "available": True, "unavailableReason": ""}],
            "terminalRuntimes": ["claude"], "terminal": True, "pty": True,
            "cwdRoots": ["C:/Docker"],
        }
        body.update(overrides)
        return body

    def _post_beat(self, headers=None, **overrides):
        response = self.client.post("/api/v1/environments/heartbeat", json=self._beat(**overrides),
                                    headers=headers or {})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def _record_beat(self, headers=None, **overrides):
        return self._record("post", "/api/v1/environments/heartbeat", json=self._beat(**overrides),
                            headers=headers or {})

    LEGACY = {"bridgeId": "bridge-legacy",
              "metadata": {"bridgeStartedAt": "2026-09-03T00:10:00Z", "bridgeBuild": "legacy-build"}}
    HOST_TIER = {"bridgeId": "env-host-tier",
                 "metadata": {"bridgeStartedAt": "2026-09-03T00:05:00Z", "bridgeKind": "aify-env",
                              "launchRoots": ["C:/Docker"]}}

    def record_first_beat(self):
        return self._record_beat(**self.LEGACY)

    def record_repeat_beat(self):
        self._post_beat(**self.LEGACY)
        return self._record_beat(**self.LEGACY)

    def record_recovery_beat_with_held_terminals(self):
        """offline -> online reads the bound agents; a held-terminal list runs the post-commit cleanup."""
        self._post_beat(status="offline", **self.LEGACY)
        recovery = {**self.LEGACY, "metadata": {**self.LEGACY["metadata"], "heldTerminals": []}}
        return self._record_beat(**recovery)

    def record_host_tier_beat(self):
        self._post_beat(**self.LEGACY)
        return self._record_beat(**self.HOST_TIER)

    def record_legacy_beat_refused_by_host_tier(self):
        self._post_beat(**self.HOST_TIER)
        return self._record_beat(**self.LEGACY)

    def record_advertisement_preserves_bridge_metadata(self):
        self._post_beat(**self.LEGACY)
        # No bridgeId: an advertisement. Its forged `bridgeBuild` must be dropped and the stored one
        # kept, and the host facts it omits must survive.
        return self._record("post", "/api/v1/environments/heartbeat", json={
            "id": ENV, "kind": "windows", "machineId": MACHINE,
            "metadata": {"bridgeBuild": "forged", "launchRoots": ["D:/work"]},
        })

    def record_beat_refused_by_host_proof(self):
        self._post_beat(headers={"X-Aify-Host-Proof": "the-real-host"}, **self.LEGACY)
        return self._record_beat(headers={"X-Aify-Host-Proof": "someone-else"}, **self.LEGACY)

    def _seed_analytics(self) -> None:
        for agent_id in ("freeze-alpha", "freeze-beta"):
            response = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)
        now_ms = int(time.time() * 1000)
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        old_iso = "2026-01-02T03:04:05Z"
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen) VALUES (?,?,?,?,?)",
                ("env_freeze", "freeze-machine", "bridge_freeze", now_iso, now_iso),
            )
            for i in range(3):
                conn.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"fz-msg-{i}", "freeze-alpha", "freeze-beta", "direct", "request", f"s{i}", "b", "normal",
                     now_ms - i * 1000),
                )
            conn.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, timestamp, channel) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("fz-chan-0", "freeze-alpha", None, "channel", "info", "c", "b", "normal", now_ms - 500, "general"),
            )
            conn.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, timestamp) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("fz-msg-old", "freeze-beta", "freeze-alpha", "direct", "request", "old", "b", "normal",
                 1735787045000),
            )
            runs = (
                ("fz-run-0", "completed", now_iso, now_iso, 0, ""),
                ("fz-run-1", "completed", now_iso, now_iso, 0, ""),
                ("fz-run-2", "failed", now_iso, now_iso, 0, "the worker exited"),
                ("fz-run-3", "running", old_iso, None, 1, ""),
                ("fz-run-4", "completed", old_iso, old_iso, 0, ""),
            )
            for run_id, status, requested, finished, require_reply, error_text in runs:
                conn.execute(
                    "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, requested_at, "
                    "finished_at, require_reply, error_text) VALUES (?,?,?,?,?,?,?,?,?)",
                    (run_id, "fz-msg-0", "freeze-alpha", "freeze-beta", status, requested, finished,
                     require_reply, error_text),
                )
            conn.commit()
        finally:
            conn.close()

    def record_analytics_hour(self):
        self._seed_analytics()
        return self._record("get", "/api/v1/analytics?range=hour")

    def record_analytics_all(self):
        self._seed_analytics()
        return self._record("get", "/api/v1/analytics?range=all")

    # ------------------------------------------------------------------ controls

    def test_the_recorder_records(self):
        """POSITIVE CONTROL: a recorder that intercepted nothing would match an empty expectation."""
        _status, _body, log = self.record_first_beat()
        self.assertGreater(len(log), 5, log)
        self.assertTrue(any("BEGIN IMMEDIATE" in str(entry) for entry in log), log)

    def test_the_recorder_can_say_no(self):
        """NEGATIVE CONTROL: two different requests must record two different sequences."""
        _s1, _b1, first = self.record_first_beat()
        _s2, _b2, refused = self.record_beat_refused_by_host_proof()
        self.assertNotEqual(first, refused)

    # ------------------------------------------------------------------ heartbeat

    def _assert_frozen(self, recorded, expected_status, expected_body, expected_log):
        status, body, log = recorded
        self.assertEqual(log, expected_log)
        self.assertEqual(status, expected_status)
        self.assertEqual(_norm_value(body), expected_body)

    def test_first_beat_creates_the_environment(self):
        self._assert_frozen(self.record_first_beat(), 200, FIRST_BEAT_BODY, FIRST_BEAT_SQL)

    def test_repeat_beat(self):
        self._assert_frozen(self.record_repeat_beat(), 200, REPEAT_BEAT_BODY, REPEAT_BEAT_SQL)

    def test_recovery_beat_with_held_terminals(self):
        self._assert_frozen(self.record_recovery_beat_with_held_terminals(), 200, RECOVERY_BODY, RECOVERY_SQL)

    def test_host_tier_beat_supersedes_a_legacy_bridge(self):
        self._assert_frozen(self.record_host_tier_beat(), 200, HOST_TIER_BODY, HOST_TIER_SQL)

    def test_legacy_beat_is_refused_by_the_host_tier(self):
        self._assert_frozen(self.record_legacy_beat_refused_by_host_tier(), 200,
                            LEGACY_REFUSED_BODY, LEGACY_REFUSED_SQL)

    def test_advertisement_preserves_bridge_owned_metadata(self):
        self._assert_frozen(self.record_advertisement_preserves_bridge_metadata(), 200,
                            ADVERTISEMENT_BODY, ADVERTISEMENT_SQL)

    def test_beat_refused_by_host_proof(self):
        self._assert_frozen(self.record_beat_refused_by_host_proof(), 403,
                            PROOF_REFUSED_BODY, PROOF_REFUSED_SQL)

    # ------------------------------------------------------------------ analytics

    def _assert_analytics(self, recorded, expected_body, expected_log):
        status, body, log = recorded
        self.assertEqual(log, expected_log)
        self.assertEqual(status, 200)
        self.assertEqual(_summarise_analytics(body), expected_body)

    def test_analytics_range_hour(self):
        self._assert_analytics(self.record_analytics_hour(), ANALYTICS_HOUR_BODY, ANALYTICS_HOUR_SQL)

    def test_analytics_range_all(self):
        self._assert_analytics(self.record_analytics_all(), ANALYTICS_ALL_BODY, ANALYTICS_ALL_SQL)


# ---------------------------------------------------------------------- recorded at 2223c45b
# Generated by running each record_* scenario above against commit 2223c45b, then pasted verbatim.

#: status 200; 10 recorded calls
FIRST_BEAT_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 SQL INSERT INTO environments ( id, label, machine_id, os, kind, bridge_id, bridge_version, '
 'launcher_version, launcher_registry_fingerprint, cwd_roots, runtimes, status, metadata, registered_at, '
 'last_seen ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) | ["windows:freeze-host:default", "Freeze host", '
 '"win32:freeze-host", "windows", "windows", "bridge-legacy", "", "", "", "[\\"C:/Docker\\"]", '
 '"[{\\"runtime\\": \\"claude-code\\", \\"available\\": true, \\"unavailableReason\\": \\"\\"}]", "online", '
 '"{\\"bridgeStartedAt\\": \\"<iso>\\", \\"bridgeBuild\\": \\"legacy-build\\", \\"terminal\\": true, '
 '\\"pty\\": true, \\"terminalRuntimes\\": [\\"claude-code\\"], \\"advertisedCwdRoots\\": [\\"C:/Docker\\"], '
 '\\"bridgeLastSeen\\": \\"<iso>\\"}", "<iso>", "<iso>"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 COMMIT',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'WS environment_heartbeat {"environmentId": "windows:freeze-host:default", "bridgeId": "bridge-legacy"}',
 'c1 CLOSE']
FIRST_BEAT_BODY = {'claimer': {'accepted': True, 'bridgeId': 'bridge-legacy', 'reason': ''},
 'environment': {'bridgeId': 'bridge-legacy',
                 'bridgeVersion': '',
                 'codeCurrency': {'onDisk': '', 'running': '', 'state': 'unknown'},
                 'cwdRoots': ['C:/Docker'],
                 'definitions': None,
                 'id': 'windows:freeze-host:default',
                 'kind': 'windows',
                 'label': 'Freeze host',
                 'lastSeen': '<iso>',
                 'launcherRegistryFingerprint': '',
                 'launcherVersion': '',
                 'machineId': 'win32:freeze-host',
                 'metadata': {'advertisedCwdRoots': ['C:/Docker'],
                              'bridgeBuild': 'legacy-build',
                              'bridgeLastSeen': '<iso>',
                              'bridgeStartedAt': '<iso>',
                              'pty': True,
                              'terminal': True,
                              'terminalRuntimes': ['claude-code']},
                 'os': 'windows',
                 'pty': True,
                 'registeredAt': '<iso>',
                 'runtimes': [{'available': True,
                               'modes': ['managed-warm'],
                               'runtime': 'claude-code',
                               'unavailableReason': ''}],
                 'spawnClaim': {'bridgeLastSeen': '<iso>', 'canClaim': True, 'state': 'fresh'},
                 'status': 'online',
                 'terminal': True,
                 'terminalRuntimes': ['claude-code']},
 'ok': True}

#: status 200; 10 recorded calls
REPEAT_BEAT_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 SQL UPDATE environments SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?, bridge_version '
 '= ?, launcher_version = ?, launcher_registry_fingerprint = ?, cwd_roots = ?, runtimes = ?, status = ?, '
 'metadata = ?, last_seen = ? WHERE id = ? | ["Freeze host", "win32:freeze-host", "windows", "windows", '
 '"bridge-legacy", "", "", "", "[\\"C:/Docker\\"]", "[{\\"runtime\\": \\"claude-code\\", \\"available\\": '
 'true, \\"unavailableReason\\": \\"\\"}]", "online", "{\\"bridgeStartedAt\\": \\"<iso>\\", '
 '\\"bridgeBuild\\": \\"legacy-build\\", \\"terminal\\": true, \\"pty\\": true, \\"terminalRuntimes\\": '
 '[\\"claude-code\\"], \\"advertisedCwdRoots\\": [\\"C:/Docker\\"], \\"bridgeLastSeen\\": \\"<iso>\\"}", '
 '"<iso>", "windows:freeze-host:default"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 COMMIT',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'WS environment_heartbeat {"environmentId": "windows:freeze-host:default", "bridgeId": "bridge-legacy"}',
 'c1 CLOSE']
REPEAT_BEAT_BODY = {'claimer': {'accepted': True, 'bridgeId': 'bridge-legacy', 'reason': ''},
 'environment': {'bridgeId': 'bridge-legacy',
                 'bridgeVersion': '',
                 'codeCurrency': {'onDisk': '', 'running': '', 'state': 'unknown'},
                 'cwdRoots': ['C:/Docker'],
                 'definitions': None,
                 'id': 'windows:freeze-host:default',
                 'kind': 'windows',
                 'label': 'Freeze host',
                 'lastSeen': '<iso>',
                 'launcherRegistryFingerprint': '',
                 'launcherVersion': '',
                 'machineId': 'win32:freeze-host',
                 'metadata': {'advertisedCwdRoots': ['C:/Docker'],
                              'bridgeBuild': 'legacy-build',
                              'bridgeLastSeen': '<iso>',
                              'bridgeStartedAt': '<iso>',
                              'pty': True,
                              'terminal': True,
                              'terminalRuntimes': ['claude-code']},
                 'os': 'windows',
                 'pty': True,
                 'registeredAt': '<iso>',
                 'runtimes': [{'available': True,
                               'modes': ['managed-warm'],
                               'runtime': 'claude-code',
                               'unavailableReason': ''}],
                 'spawnClaim': {'bridgeLastSeen': '<iso>', 'canClaim': True, 'state': 'fresh'},
                 'status': 'online',
                 'terminal': True,
                 'terminalRuntimes': ['claude-code']},
 'ok': True}

#: status 200; 12 recorded calls
RECOVERY_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 SQL UPDATE environments SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?, bridge_version '
 '= ?, launcher_version = ?, launcher_registry_fingerprint = ?, cwd_roots = ?, runtimes = ?, status = ?, '
 'metadata = ?, last_seen = ? WHERE id = ? | ["Freeze host", "win32:freeze-host", "windows", "windows", '
 '"bridge-legacy", "", "", "", "[\\"C:/Docker\\"]", "[{\\"runtime\\": \\"claude-code\\", \\"available\\": '
 'true, \\"unavailableReason\\": \\"\\"}]", "online", "{\\"bridgeStartedAt\\": \\"<iso>\\", '
 '\\"bridgeBuild\\": \\"legacy-build\\", \\"terminal\\": true, \\"pty\\": true, \\"terminalRuntimes\\": '
 '[\\"claude-code\\"], \\"advertisedCwdRoots\\": [\\"C:/Docker\\"], \\"bridgeLastSeen\\": \\"<iso>\\"}", '
 '"<iso>", "windows:freeze-host:default"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 SQL SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ? | '
 '["windows:freeze-host:default"]',
 'c1 COMMIT',
 'c1 SQL SELECT id, session_id, agent_id, status, updated_at, command, bridge_id FROM terminal_sessions '
 "WHERE environment_id = ? AND status IN ('active', 'attached', 'idle', 'recovering', 'running', 'starting') "
 '| ["windows:freeze-host:default"]',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'WS environment_heartbeat {"environmentId": "windows:freeze-host:default", "bridgeId": "bridge-legacy"}',
 'c1 CLOSE']
RECOVERY_BODY = {'claimer': {'accepted': True, 'bridgeId': 'bridge-legacy', 'reason': ''},
 'environment': {'bridgeId': 'bridge-legacy',
                 'bridgeVersion': '',
                 'codeCurrency': {'onDisk': '', 'running': '', 'state': 'unknown'},
                 'cwdRoots': ['C:/Docker'],
                 'definitions': None,
                 'id': 'windows:freeze-host:default',
                 'kind': 'windows',
                 'label': 'Freeze host',
                 'lastSeen': '<iso>',
                 'launcherRegistryFingerprint': '',
                 'launcherVersion': '',
                 'machineId': 'win32:freeze-host',
                 'metadata': {'advertisedCwdRoots': ['C:/Docker'],
                              'bridgeBuild': 'legacy-build',
                              'bridgeLastSeen': '<iso>',
                              'bridgeStartedAt': '<iso>',
                              'pty': True,
                              'terminal': True,
                              'terminalRuntimes': ['claude-code']},
                 'os': 'windows',
                 'pty': True,
                 'registeredAt': '<iso>',
                 'runtimes': [{'available': True,
                               'modes': ['managed-warm'],
                               'runtime': 'claude-code',
                               'unavailableReason': ''}],
                 'spawnClaim': {'bridgeLastSeen': '<iso>', 'canClaim': True, 'state': 'fresh'},
                 'status': 'online',
                 'terminal': True,
                 'terminalRuntimes': ['claude-code']},
 'ok': True}

#: status 200; 13 recorded calls
HOST_TIER_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 SQL UPDATE environments SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?, bridge_version '
 '= ?, launcher_version = ?, launcher_registry_fingerprint = ?, cwd_roots = ?, runtimes = ?, status = ?, '
 'metadata = ?, last_seen = ? WHERE id = ? | ["Freeze host", "win32:freeze-host", "windows", "windows", '
 '"env-host-tier", "", "", "", "[\\"C:/Docker\\"]", "[{\\"runtime\\": \\"claude-code\\", \\"available\\": '
 'true, \\"unavailableReason\\": \\"\\"}]", "online", "{\\"bridgeStartedAt\\": \\"<iso>\\", '
 '\\"bridgeKind\\": \\"aify-env\\", \\"launchRoots\\": [\\"C:/Docker\\"], \\"terminal\\": true, \\"pty\\": '
 'true, \\"terminalRuntimes\\": [\\"claude-code\\"], \\"advertisedCwdRoots\\": [\\"C:/Docker\\"], '
 '\\"bridgeLastSeen\\": \\"<iso>\\"}", "<iso>", "windows:freeze-host:default"]',
 "c1 SQL UPDATE environment_controls SET status = 'failed', handled_at = ?, error = 'stale superseded-bridge "
 "stop drained (target bridge never claimed)' WHERE environment_id = ? AND action = 'stop' AND status = "
 '\'pending\' AND requested_by = \'server:superseded-bridge\' AND requested_at < ? | ["<iso>", '
 '"windows:freeze-host:default", "<iso>"]',
 "c1 SQL SELECT id FROM environment_controls WHERE environment_id = ? AND bridge_id = ? AND action = 'stop' "
 'AND status IN (\'pending\', \'claimed\') LIMIT 1 | ["windows:freeze-host:default", "bridge-legacy"]',
 'c1 SQL INSERT INTO environment_controls ( id, environment_id, bridge_id, machine_id, action, status, '
 'requested_by, requested_at ) VALUES (?,?,?,?,?,?,?,?) | ["envctl-<uuid>", "windows:freeze-host:default", '
 '"bridge-legacy", "win32:freeze-host", "stop", "pending", "server:superseded-bridge", "<iso>"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 COMMIT',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'WS environment_heartbeat {"environmentId": "windows:freeze-host:default", "bridgeId": "env-host-tier"}',
 'c1 CLOSE']
HOST_TIER_BODY = {'claimer': {'accepted': True, 'bridgeId': 'env-host-tier', 'reason': ''},
 'environment': {'bridgeId': 'env-host-tier',
                 'bridgeVersion': '',
                 'codeCurrency': {'onDisk': '', 'running': '', 'state': 'unknown'},
                 'cwdRoots': ['C:/Docker'],
                 'definitions': None,
                 'id': 'windows:freeze-host:default',
                 'kind': 'windows',
                 'label': 'Freeze host',
                 'lastSeen': '<iso>',
                 'launcherRegistryFingerprint': '',
                 'launcherVersion': '',
                 'machineId': 'win32:freeze-host',
                 'metadata': {'advertisedCwdRoots': ['C:/Docker'],
                              'bridgeKind': 'aify-env',
                              'bridgeLastSeen': '<iso>',
                              'bridgeStartedAt': '<iso>',
                              'launchRoots': ['C:/Docker'],
                              'pty': True,
                              'terminal': True,
                              'terminalRuntimes': ['claude-code']},
                 'os': 'windows',
                 'pty': True,
                 'registeredAt': '<iso>',
                 'runtimes': [{'available': True,
                               'modes': ['managed-warm'],
                               'runtime': 'claude-code',
                               'unavailableReason': ''}],
                 'spawnClaim': {'bridgeLastSeen': '<iso>', 'canClaim': True, 'state': 'fresh'},
                 'status': 'online',
                 'terminal': True,
                 'terminalRuntimes': ['claude-code']},
 'ok': True}

#: status 200; 5 recorded calls
LEGACY_REFUSED_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 CLOSE']
LEGACY_REFUSED_BODY = {'claimer': {'accepted': False,
             'bridgeId': 'env-host-tier',
             'reason': 'this environment is held by the aify-env host tier, which outranks an aify-comms '
                       'environment bridge. That bridge is retired: re-run install.sh on this host and '
                       'relaunch its wrappers.'},
 'environment': {'bridgeId': 'env-host-tier',
                 'bridgeVersion': '',
                 'codeCurrency': {'onDisk': '', 'running': '', 'state': 'unknown'},
                 'cwdRoots': ['C:/Docker'],
                 'definitions': None,
                 'id': 'windows:freeze-host:default',
                 'kind': 'windows',
                 'label': 'Freeze host',
                 'lastSeen': '<iso>',
                 'launcherRegistryFingerprint': '',
                 'launcherVersion': '',
                 'machineId': 'win32:freeze-host',
                 'metadata': {'advertisedCwdRoots': ['C:/Docker'],
                              'bridgeKind': 'aify-env',
                              'bridgeLastSeen': '<iso>',
                              'bridgeStartedAt': '<iso>',
                              'launchRoots': ['C:/Docker'],
                              'pty': True,
                              'terminal': True,
                              'terminalRuntimes': ['claude-code']},
                 'os': 'windows',
                 'pty': True,
                 'registeredAt': '<iso>',
                 'runtimes': [{'available': True,
                               'modes': ['managed-warm'],
                               'runtime': 'claude-code',
                               'unavailableReason': ''}],
                 'spawnClaim': {'bridgeLastSeen': '<iso>', 'canClaim': True, 'state': 'fresh'},
                 'status': 'online',
                 'terminal': True,
                 'terminalRuntimes': ['claude-code']},
 'ok': True}

#: status 200; 10 recorded calls
ADVERTISEMENT_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 SQL UPDATE environments SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?, bridge_version '
 '= ?, launcher_version = ?, launcher_registry_fingerprint = ?, cwd_roots = ?, runtimes = ?, status = ?, '
 'metadata = ?, last_seen = ? WHERE id = ? | ["Freeze host", "win32:freeze-host", "windows", "windows", '
 '"bridge-legacy", "", "", "", "[\\"C:/Docker\\"]", "[{\\"runtime\\": \\"claude-code\\", \\"available\\": '
 'true, \\"unavailableReason\\": \\"\\"}]", "online", "{\\"launchRoots\\": [\\"D:/work\\"], '
 '\\"advertisedCwdRoots\\": [\\"C:/Docker\\"], \\"bridgeStartedAt\\": \\"<iso>\\", \\"bridgeLastSeen\\": '
 '\\"<iso>\\", \\"bridgeBuild\\": \\"legacy-build\\", \\"terminal\\": true, \\"pty\\": true, '
 '\\"terminalRuntimes\\": [\\"claude-code\\"]}", "<iso>", "windows:freeze-host:default"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 COMMIT',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'WS environment_heartbeat {"environmentId": "windows:freeze-host:default", "bridgeId": ""}',
 'c1 CLOSE']
ADVERTISEMENT_BODY = {'claimer': {'accepted': False,
             'bridgeId': '',
             'reason': 'no bridgeId: this beat describes the host, it does not claim work'},
 'environment': {'bridgeId': 'bridge-legacy',
                 'bridgeVersion': '',
                 'codeCurrency': {'onDisk': '', 'running': '', 'state': 'unknown'},
                 'cwdRoots': ['C:/Docker'],
                 'definitions': None,
                 'id': 'windows:freeze-host:default',
                 'kind': 'windows',
                 'label': 'Freeze host',
                 'lastSeen': '<iso>',
                 'launcherRegistryFingerprint': '',
                 'launcherVersion': '',
                 'machineId': 'win32:freeze-host',
                 'metadata': {'advertisedCwdRoots': ['C:/Docker'],
                              'bridgeBuild': 'legacy-build',
                              'bridgeLastSeen': '<iso>',
                              'bridgeStartedAt': '<iso>',
                              'launchRoots': ['D:/work'],
                              'pty': True,
                              'terminal': True,
                              'terminalRuntimes': ['claude-code']},
                 'os': 'windows',
                 'pty': True,
                 'registeredAt': '<iso>',
                 'runtimes': [{'available': True,
                               'modes': ['managed-warm'],
                               'runtime': 'claude-code',
                               'unavailableReason': ''}],
                 'spawnClaim': {'bridgeLastSeen': '<iso>', 'canClaim': True, 'state': 'fresh'},
                 'status': 'online',
                 'terminal': True,
                 'terminalRuntimes': ['claude-code']},
 'ok': True}

#: status 403; 5 recorded calls
PROOF_REFUSED_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL BEGIN IMMEDIATE',
 'c1 SQL SELECT * FROM environments WHERE id = ? | ["windows:freeze-host:default"]',
 'c1 SQL SELECT proof_digest FROM host_proofs WHERE machine_id = ? | ["win32:freeze-host"]',
 'c1 CLOSE']
PROOF_REFUSED_BODY = {'detail': 'environment heartbeat refused: machine win32:freeze-host proves itself with a host proof and '
           "this request presents a different one. If this is that machine's aify-env after its "
           '~/.aify/host-secret was replaced, an operator resets it: POST '
           '/host-proofs/win32:freeze-host/reset'}

#: status 200; 96 recorded calls
ANALYTICS_HOUR_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL SELECT key, value FROM settings',
 (66,
  "c1 SQL SELECT COUNT(*) FROM messages WHERE ( (source = 'direct' AND to_agent IS NOT NULL) OR (source = "
  '\'channel\' AND to_agent IS NULL) ) AND timestamp >= ? AND timestamp < ? | ["<epoch>", "<epoch>"]'),
 "c1 SQL SELECT strftime('%Y-%m', datetime(timestamp / 1000, 'unixepoch')) AS bucket, MIN(timestamp) AS "
 "start_ms, COUNT(*) AS cnt FROM messages WHERE ( (source = 'direct' AND to_agent IS NOT NULL) OR (source = "
 "'channel' AND to_agent IS NULL) ) GROUP BY bucket ORDER BY bucket ASC",
 'c1 SQL SELECT status, COUNT(*) as cnt FROM dispatch_runs WHERE COALESCE(finished_at, requested_at) >= ? '
 'GROUP BY status | ["<iso>"]',
 'c1 SQL SELECT status, COUNT(*) as cnt FROM spawn_requests WHERE updated_at >= ? GROUP BY status | '
 '["<iso>"]',
 'c1 SQL SELECT * FROM agents',
 'c1 SQL SELECT agent_id, environment_id FROM agent_sessions WHERE status IN (?, ?, ?, ?, ?) ORDER BY '
 'last_seen DESC | ["cli-takeover", "recovering", "restarting", "running", "starting"]',
 'c1 SQL SELECT agent_id, in_turn, awaiting_input, last_event_at, turn_started_at FROM agent_status_state '
 'WHERE agent_id IN (?,?) | ["freeze-alpha", "freeze-beta"]',
 'c1 SQL SELECT agent_id, working_at, subagents_at, background_at FROM agent_console_signal WHERE agent_id '
 'IN (?,?) | ["freeze-alpha", "freeze-beta"]',
 'c1 SQL SELECT agent_id, turn_busy, turn_runtime, turn_updated_at, ready, turn_started_at, turn_bridge_id '
 'FROM agent_turn_state WHERE agent_id IN (?,?) | ["freeze-alpha", "freeze-beta"]',
 'c1 SQL SELECT agent_id, activity_state, activity_reported_at FROM terminal_sessions WHERE agent_id IN '
 "(?,?) AND status IN (?,?,?,?,?) AND COALESCE(activity_state, '') != '' ORDER BY activity_reported_at DESC, "
 'id DESC | ["freeze-alpha", "freeze-beta", "active", "attached", "idle", "running", "starting"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 SQL SELECT * FROM agent_sessions WHERE agent_id = ? AND status NOT IN (?, ?, ?, ?, ?, ?) ORDER BY CASE '
 "WHEN status IN ('running', 'recovering', 'restarting', 'cli-takeover') THEN 0 ELSE 1 END, last_seen DESC, "
 'started_at DESC LIMIT 1 | ["freeze-alpha", "cancelled", "completed", "ended", "failed", "lost", "stopped"]',
 'c1 SQL SELECT id, status, subject, from_agent, dispatch_mode, execution_mode, runtime, requested_at, '
 'claimed_at, started_at, claim_bridge_id FROM dispatch_runs WHERE target_agent = ? AND status IN '
 "('claimed', 'running') ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC LIMIT 1 | "
 '["freeze-alpha"]',
 'c1 SQL SELECT id, subject, from_agent, execution_mode, runtime, requested_at, claimed_at, started_at FROM '
 "dispatch_runs WHERE target_agent = ? AND status = 'delivered' AND execution_mode IN ('channel', "
 "'resident') AND require_reply = 1 ORDER BY COALESCE(started_at, claimed_at, requested_at) DESC LIMIT 1 | "
 '["freeze-alpha"]',
 "c1 SQL SELECT last_seen FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar' AND "
 'COALESCE(superseded_by, \'\') = \'\' ORDER BY last_seen DESC LIMIT 1 | ["freeze-alpha"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 SQL SELECT * FROM agent_sessions WHERE agent_id = ? AND status NOT IN (?, ?, ?, ?, ?, ?) ORDER BY CASE '
 "WHEN status IN ('running', 'recovering', 'restarting', 'cli-takeover') THEN 0 ELSE 1 END, last_seen DESC, "
 'started_at DESC LIMIT 1 | ["freeze-beta", "cancelled", "completed", "ended", "failed", "lost", "stopped"]',
 'c1 SQL SELECT id, status, subject, from_agent, dispatch_mode, execution_mode, runtime, requested_at, '
 'claimed_at, started_at, claim_bridge_id FROM dispatch_runs WHERE target_agent = ? AND status IN '
 "('claimed', 'running') ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC LIMIT 1 | "
 '["freeze-beta"]',
 'c1 SQL SELECT id, subject, from_agent, execution_mode, runtime, requested_at, claimed_at, started_at FROM '
 "dispatch_runs WHERE target_agent = ? AND status = 'delivered' AND execution_mode IN ('channel', "
 "'resident') AND require_reply = 1 ORDER BY COALESCE(started_at, claimed_at, requested_at) DESC LIMIT 1 | "
 '["freeze-beta"]',
 "c1 SQL SELECT last_seen FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar' AND "
 'COALESCE(superseded_by, \'\') = \'\' ORDER BY last_seen DESC LIMIT 1 | ["freeze-beta"]',
 'c1 SQL SELECT status, last_seen FROM environments',
 "c1 SQL SELECT requested_at, status FROM dispatch_runs WHERE require_reply = 1 AND status IN ('queued', "
 "'claimed', 'running', 'delivered') AND COALESCE(result_message_id, '') = ''",
 'c1 SQL SELECT (julianday(finished_at) - julianday(requested_at)) * 1440 AS mins FROM dispatch_runs WHERE '
 "COALESCE(finished_at, requested_at) >= ? AND status = 'completed' AND require_reply = 1 AND requested_at "
 'IS NOT NULL AND finished_at IS NOT NULL | ["<iso>"]',
 "c1 SQL SELECT date(COALESCE(finished_at, requested_at)) AS day, SUM(CASE WHEN status = 'completed' THEN 1 "
 "ELSE 0 END) AS completed, SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed "
 "FROM dispatch_runs WHERE julianday(COALESCE(finished_at, requested_at)) > julianday('now') - 14 GROUP BY "
 'day',
 "c1 SQL SELECT date('now', '-' || value || ' days') AS day FROM (SELECT 0 AS value UNION SELECT 1 UNION "
 'SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION '
 'SELECT 9 UNION SELECT 10 UNION SELECT 11 UNION SELECT 12 UNION SELECT 13) ORDER BY day',
 "c1 SQL SELECT target_agent AS agent, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed, "
 "SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed, COUNT(*) AS total FROM "
 'dispatch_runs WHERE COALESCE(finished_at, requested_at) >= ? GROUP BY target_agent ORDER BY completed '
 'DESC, total DESC LIMIT 10 | ["<iso>"]',
 "c1 SQL SELECT channel, COUNT(*) AS cnt FROM messages WHERE source = 'channel' AND channel IS NOT NULL AND "
 'TRIM(channel) != \'\' AND timestamp >= ? GROUP BY channel ORDER BY cnt DESC LIMIT 8 | ["<epoch>"]',
 "c1 SQL SELECT COALESCE(NULLIF(TRIM(error_text), ''), '(no error text recorded)') AS reason, COUNT(*) AS "
 "cnt FROM dispatch_runs WHERE COALESCE(finished_at, requested_at) >= ? AND status IN ('failed', "
 '\'cancelled\') GROUP BY reason ORDER BY cnt DESC LIMIT 8 | ["<iso>"]',
 'c1 CLOSE']
ANALYTICS_HOUR_BODY = {'agentLeaderboard': [{'agent': 'freeze-beta', 'completed': 2, 'failed': 1, 'successRate': 66.7, 'total': 3}],
 'busiestChannels': [{'channel': 'general', 'count': 1}],
 'dispatchOutcomes': {'completed': 2, 'failed': 1, 'len': 14},
 'failureReasons': [{'count': 1, 'reason': 'the worker exited'}],
 'fleetMedianReplyMinutes': None,
 'liveAgents': 0,
 'messageTotal': 4,
 'messagesPerAllTime': {'len': 2, 'total': 5},
 'messagesPerDay': {'len': 30, 'total': 4},
 'messagesPerHour': {'len': 24, 'total': 4},
 'messagesPerMonth': {'len': 12, 'total': 4},
 'ok': True,
 'onlineAgents': 0,
 'onlineEnvironments': 1,
 'openReplyContracts': 1,
 'overdueAfterMinutes': 10,
 'overdueReplyContracts': 1,
 'range': 'hour',
 'rangeLabel': 'last 24 hours',
 'runTotal': 3,
 'runsByStatus': {'completed': 2, 'failed': 1},
 'runsCompleted': 2,
 'runsFailed': 1,
 'spawnRequestTotal': 0,
 'spawnRequestsByStatus': {},
 'successRate': 66.7,
 'workingAgents': 0}

#: status 200; 96 recorded calls
ANALYTICS_ALL_SQL = ['c1 SCRIPT PRAGMA busy_timeout=5000;PRAGMA synchronous=NORMAL;PRAGMA foreign_keys=ON;PRAGMA '
 'journal_size_limit=16777216;',
 'c1 SQL SELECT key, value FROM settings',
 (66,
  "c1 SQL SELECT COUNT(*) FROM messages WHERE ( (source = 'direct' AND to_agent IS NOT NULL) OR (source = "
  '\'channel\' AND to_agent IS NULL) ) AND timestamp >= ? AND timestamp < ? | ["<epoch>", "<epoch>"]'),
 "c1 SQL SELECT strftime('%Y-%m', datetime(timestamp / 1000, 'unixepoch')) AS bucket, MIN(timestamp) AS "
 "start_ms, COUNT(*) AS cnt FROM messages WHERE ( (source = 'direct' AND to_agent IS NOT NULL) OR (source = "
 "'channel' AND to_agent IS NULL) ) GROUP BY bucket ORDER BY bucket ASC",
 'c1 SQL SELECT status, COUNT(*) as cnt FROM dispatch_runs GROUP BY status',
 'c1 SQL SELECT status, COUNT(*) as cnt FROM spawn_requests GROUP BY status',
 'c1 SQL SELECT * FROM agents',
 'c1 SQL SELECT agent_id, environment_id FROM agent_sessions WHERE status IN (?, ?, ?, ?, ?) ORDER BY '
 'last_seen DESC | ["cli-takeover", "recovering", "restarting", "running", "starting"]',
 'c1 SQL SELECT agent_id, in_turn, awaiting_input, last_event_at, turn_started_at FROM agent_status_state '
 'WHERE agent_id IN (?,?) | ["freeze-alpha", "freeze-beta"]',
 'c1 SQL SELECT agent_id, working_at, subagents_at, background_at FROM agent_console_signal WHERE agent_id '
 'IN (?,?) | ["freeze-alpha", "freeze-beta"]',
 'c1 SQL SELECT agent_id, turn_busy, turn_runtime, turn_updated_at, ready, turn_started_at, turn_bridge_id '
 'FROM agent_turn_state WHERE agent_id IN (?,?) | ["freeze-alpha", "freeze-beta"]',
 'c1 SQL SELECT agent_id, activity_state, activity_reported_at FROM terminal_sessions WHERE agent_id IN '
 "(?,?) AND status IN (?,?,?,?,?) AND COALESCE(activity_state, '') != '' ORDER BY activity_reported_at DESC, "
 'id DESC | ["freeze-alpha", "freeze-beta", "active", "attached", "idle", "running", "starting"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 SQL SELECT * FROM agent_sessions WHERE agent_id = ? AND status NOT IN (?, ?, ?, ?, ?, ?) ORDER BY CASE '
 "WHEN status IN ('running', 'recovering', 'restarting', 'cli-takeover') THEN 0 ELSE 1 END, last_seen DESC, "
 'started_at DESC LIMIT 1 | ["freeze-alpha", "cancelled", "completed", "ended", "failed", "lost", "stopped"]',
 'c1 SQL SELECT id, status, subject, from_agent, dispatch_mode, execution_mode, runtime, requested_at, '
 'claimed_at, started_at, claim_bridge_id FROM dispatch_runs WHERE target_agent = ? AND status IN '
 "('claimed', 'running') ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC LIMIT 1 | "
 '["freeze-alpha"]',
 'c1 SQL SELECT id, subject, from_agent, execution_mode, runtime, requested_at, claimed_at, started_at FROM '
 "dispatch_runs WHERE target_agent = ? AND status = 'delivered' AND execution_mode IN ('channel', "
 "'resident') AND require_reply = 1 ORDER BY COALESCE(started_at, claimed_at, requested_at) DESC LIMIT 1 | "
 '["freeze-alpha"]',
 "c1 SQL SELECT last_seen FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar' AND "
 'COALESCE(superseded_by, \'\') = \'\' ORDER BY last_seen DESC LIMIT 1 | ["freeze-alpha"]',
 'c1 SQL SELECT key, value FROM settings',
 'c1 SQL SELECT * FROM agent_sessions WHERE agent_id = ? AND status NOT IN (?, ?, ?, ?, ?, ?) ORDER BY CASE '
 "WHEN status IN ('running', 'recovering', 'restarting', 'cli-takeover') THEN 0 ELSE 1 END, last_seen DESC, "
 'started_at DESC LIMIT 1 | ["freeze-beta", "cancelled", "completed", "ended", "failed", "lost", "stopped"]',
 'c1 SQL SELECT id, status, subject, from_agent, dispatch_mode, execution_mode, runtime, requested_at, '
 'claimed_at, started_at, claim_bridge_id FROM dispatch_runs WHERE target_agent = ? AND status IN '
 "('claimed', 'running') ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC LIMIT 1 | "
 '["freeze-beta"]',
 'c1 SQL SELECT id, subject, from_agent, execution_mode, runtime, requested_at, claimed_at, started_at FROM '
 "dispatch_runs WHERE target_agent = ? AND status = 'delivered' AND execution_mode IN ('channel', "
 "'resident') AND require_reply = 1 ORDER BY COALESCE(started_at, claimed_at, requested_at) DESC LIMIT 1 | "
 '["freeze-beta"]',
 "c1 SQL SELECT last_seen FROM bridge_instances WHERE agent_id = ? AND bridge_kind = 'channel-sidecar' AND "
 'COALESCE(superseded_by, \'\') = \'\' ORDER BY last_seen DESC LIMIT 1 | ["freeze-beta"]',
 'c1 SQL SELECT status, last_seen FROM environments',
 "c1 SQL SELECT requested_at, status FROM dispatch_runs WHERE require_reply = 1 AND status IN ('queued', "
 "'claimed', 'running', 'delivered') AND COALESCE(result_message_id, '') = ''",
 'c1 SQL SELECT (julianday(finished_at) - julianday(requested_at)) * 1440 AS mins FROM dispatch_runs WHERE '
 "status = 'completed' AND require_reply = 1 AND requested_at IS NOT NULL AND finished_at IS NOT NULL",
 "c1 SQL SELECT date(COALESCE(finished_at, requested_at)) AS day, SUM(CASE WHEN status = 'completed' THEN 1 "
 "ELSE 0 END) AS completed, SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed "
 "FROM dispatch_runs WHERE julianday(COALESCE(finished_at, requested_at)) > julianday('now') - 14 GROUP BY "
 'day',
 "c1 SQL SELECT date('now', '-' || value || ' days') AS day FROM (SELECT 0 AS value UNION SELECT 1 UNION "
 'SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION '
 'SELECT 9 UNION SELECT 10 UNION SELECT 11 UNION SELECT 12 UNION SELECT 13) ORDER BY day',
 "c1 SQL SELECT target_agent AS agent, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed, "
 "SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed, COUNT(*) AS total FROM "
 'dispatch_runs GROUP BY target_agent ORDER BY completed DESC, total DESC LIMIT 10',
 "c1 SQL SELECT channel, COUNT(*) AS cnt FROM messages WHERE source = 'channel' AND channel IS NOT NULL AND "
 "TRIM(channel) != '' GROUP BY channel ORDER BY cnt DESC LIMIT 8",
 "c1 SQL SELECT COALESCE(NULLIF(TRIM(error_text), ''), '(no error text recorded)') AS reason, COUNT(*) AS "
 "cnt FROM dispatch_runs WHERE status IN ('failed', 'cancelled') GROUP BY reason ORDER BY cnt DESC LIMIT 8",
 'c1 CLOSE']
ANALYTICS_ALL_BODY = {'agentLeaderboard': [{'agent': 'freeze-beta', 'completed': 3, 'failed': 1, 'successRate': 75.0, 'total': 5}],
 'busiestChannels': [{'channel': 'general', 'count': 1}],
 'dispatchOutcomes': {'completed': 2, 'failed': 1, 'len': 14},
 'failureReasons': [{'count': 1, 'reason': 'the worker exited'}],
 'fleetMedianReplyMinutes': None,
 'liveAgents': 0,
 'messageTotal': 5,
 'messagesPerAllTime': {'len': 2, 'total': 5},
 'messagesPerDay': {'len': 30, 'total': 4},
 'messagesPerHour': {'len': 24, 'total': 4},
 'messagesPerMonth': {'len': 12, 'total': 4},
 'ok': True,
 'onlineAgents': 0,
 'onlineEnvironments': 1,
 'openReplyContracts': 1,
 'overdueAfterMinutes': 10,
 'overdueReplyContracts': 1,
 'range': 'all',
 'rangeLabel': 'all time',
 'runTotal': 5,
 'runsByStatus': {'completed': 3, 'failed': 1, 'running': 1},
 'runsCompleted': 3,
 'runsFailed': 1,
 'spawnRequestTotal': 0,
 'spawnRequestsByStatus': {},
 'successRate': 75.0,
 'workingAgents': 0}
