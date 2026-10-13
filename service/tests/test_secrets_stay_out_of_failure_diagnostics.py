"""A credential in a dying terminal's last line is not stored or served (2026-10-14 audit, L3).

A hermes launcher's "gateway host ready" line carried the gateway token, became a spawn request's
stated cause and was shown on the dashboard. Synthetic sentinels only: no real credential is in here.
"""

from __future__ import annotations

import unittest

from service.api_core.records import _serialize_dispatch_run_row, _terminal_session_to_dict
from service.api_core.spawn_requests_io import _spawn_request_to_dict
from service.secret_redaction import REDACTED, redact_secrets
from service.terminal_diagnostics import meaningful_failure_line

SENTINEL = "SENTINELsecretValue0123456789"


class _Row(dict):
    """A sqlite3.Row stand-in: `row["x"]` and `row.keys()`, with absent columns read as empty."""

    def __getitem__(self, key):
        return self.get(key, "")


class SecretsStayOutOfFailureDiagnostics(unittest.TestCase):
    def test_every_credential_shape_is_redacted_and_its_name_kept(self):
        shapes = [
            f'gateway host ready: {{"port":9430,"token":"{SENTINEL}","wsUrl":"ws://127.0.0.1:9430/api/ws?token={SENTINEL}"}}',
            f"curl -H 'X-API-Key: {SENTINEL}' http://localhost:8800",
            f"Authorization: Bearer {SENTINEL}",
            f"api_key={SENTINEL}&x=1",
            f"OPENAI_API_KEY={SENTINEL}",
            f'"access_token": "{SENTINEL}"',
            f"password={SENTINEL}",
        ]
        for line in shapes:
            with self.subTest(line=line[:40]):
                out = redact_secrets(line)
                self.assertNotIn(SENTINEL, out)
                self.assertIn(REDACTED, out)
        self.assertIn('"token":"[redacted]"', redact_secrets(shapes[0]), "the name must survive, so the line still reads")

    def test_ordinary_failure_text_is_untouched(self):
        # CONTROL: the redactor says no to words that only look nearby.
        for line in [
            "fatal: connection refused on port 9430",
            "Error: token budget exceeded for this model",
            "worker exited with code 1 after 3 retries",
            "the tokenizer failed to load; see the error above",
        ]:
            with self.subTest(line=line):
                self.assertEqual(redact_secrets(line), line)

    def test_the_chosen_failure_line_is_redacted_before_it_is_stored(self):
        raw = f'[hermes-aify] managed gateway host ready: {{"port":9430,"token":"{SENTINEL}"}}\n'
        line = meaningful_failure_line(raw)
        self.assertTrue(line, "control: the line was chosen as the cause")
        self.assertNotIn(SENTINEL, line)

    def test_rows_stored_before_this_are_served_redacted(self):
        stored = f'Worker terminal term_1 is stopped: ready: {{"token":"{SENTINEL}"}}'
        served = [
            _spawn_request_to_dict(_Row(id="spawn_1", error=stored, status="failed")).get("error", ""),
            _terminal_session_to_dict(_Row(id="term_1", error=stored, status="stopped")).get("error", ""),
            _serialize_dispatch_run_row(_Row(id="run_1", error_text=stored, status="failed")).get("error", ""),
        ]
        for name, value in zip(("spawn request", "terminal", "dispatch run"), served):
            with self.subTest(record=name):
                self.assertIn("Worker terminal term_1 is stopped", value, "control: the field is the one served")
                self.assertNotIn(SENTINEL, value)


if __name__ == "__main__":
    unittest.main()
