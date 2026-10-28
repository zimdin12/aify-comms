"""The dashboard's Edit of a defined agent shows the definition's own values and sends only what changed, so the
agent record carries those values. Every one must be a field a definition request may set, or Save would 422."""
import json
import unittest

from service.api_core.definition_records import EDIT_FIELDS, definition_of
from service.api_core.definition_requests import EDITABLE_FIELDS


class _Row(dict):
    def keys(self):
        return super().keys()


def _definition_row(body):
    return _Row(machine_id="m1", store_id="s1", incarnation=1, revision=3, host_state="ok", host_problems="[]",
                available=1, unavailable_reason="", body=json.dumps(body))


class DefinitionRecordServesTheEditFields(unittest.TestCase):
    def test_each_edit_field_is_one_a_request_may_set(self):
        self.assertEqual([field for field in EDIT_FIELDS if field not in EDITABLE_FIELDS], [])

    def test_the_record_carries_the_edit_fields_and_never_env(self):
        body = {"id": "coder", "name": "Coder", "role": "coder", "harness": "claude", "workspace": "/w",
                "model": "opus", "effort": "high", "instructions": "be brief", "mode": "managed",
                "env": {"TOKEN": "x"}, "herdrSpace": True}
        record = definition_of(_Row(definition_state="defined"), _definition_row(body))
        self.assertEqual(record["fields"], {field: body[field] for field in EDIT_FIELDS})
        self.assertNotIn("env", record["fields"])
        self.assertEqual(record["harnessOfRuntime"]["claude-code"], "claude")

    def test_an_undefined_agent_has_no_fields(self):
        self.assertEqual(definition_of(_Row(definition_state=""), None)["fields"], {})


if __name__ == "__main__":
    unittest.main()
