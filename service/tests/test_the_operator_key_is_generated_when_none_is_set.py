"""`OPERATOR_KEY` is generated into the data volume when `.env` sets none, and the dashboard injects THAT key.

Before 2026-09-23 nothing ever set it, so on any host where nobody had run `openssl rand` by hand the
dashboard's delete controls refused and the service could not tell the operator sending AS an agent
from the agent. See service/api_core/operator_key_file.py.

The service and the dashboard are separate processes in separate containers; the dashboard mounts the
volume read-only. The one failure worth guarding hardest is the two holding DIFFERENT keys -- every
operator action from the dashboard would then be refused -- so both halves are tested reading one file.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from service.api_core.operator_key_file import OPERATOR_KEY_FILENAME, resolve_operator_key


class TheServiceResolvesItsOperatorKey(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)
        self.file = self.data / OPERATOR_KEY_FILENAME

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_key_set_in_the_environment_is_used_and_no_file_is_written(self) -> None:
        self.assertEqual(resolve_operator_key("from-env", self.data, create=True), "from-env")
        self.assertFalse(self.file.exists(), "a configured key must leave the data volume alone")

    def test_with_none_set_one_is_generated_and_then_kept(self) -> None:
        first = resolve_operator_key("", self.data, create=True)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(resolve_operator_key("", self.data, create=True), first,
                         "a restart must not rotate it: the open dashboard holds the old one")

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_the_generated_file_is_readable_by_its_owner_only(self) -> None:
        resolve_operator_key("", self.data, create=True)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)

    def test_a_reader_never_creates_one(self) -> None:
        self.assertEqual(resolve_operator_key("", self.data, create=False), "")
        self.assertFalse(self.file.exists())

    def test_a_volume_it_cannot_write_leaves_it_unset_rather_than_failing_startup(self) -> None:
        missing = self.data / "no-such-dir"
        self.assertEqual(resolve_operator_key("", missing, create=True), "",
                         "no key is the fail-closed answer: nobody can then claim operator privilege")


class TheDashboardInjectsTheSameKey(unittest.TestCase):
    def _page(self, *, configured: str, data_dir: Path) -> str:
        from service import new_dashboard_app

        config = SimpleNamespace(operator_key=configured, data_dir=str(data_dir))
        with mock.patch.object(new_dashboard_app, "get_config", return_value=config):
            return new_dashboard_app._index_html()

    def _injected(self, html: str) -> str:
        match = re.search(r'window\.__AIFY_OPERATOR_KEY__ = "([^"]*)"', html)
        return match.group(1) if match else ""

    def test_the_page_carries_the_key_the_service_generated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generated = resolve_operator_key("", tmp, create=True)
            self.assertEqual(self._injected(self._page(configured="", data_dir=Path(tmp))), generated)

    def test_CONTROL_a_key_from_the_environment_still_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolve_operator_key("", tmp, create=True)
            self.assertEqual(self._injected(self._page(configured="from-env", data_dir=Path(tmp))), "from-env")

    def test_CONTROL_with_no_key_anywhere_nothing_is_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = self._page(configured="", data_dir=Path(tmp))
            self.assertNotIn("__AIFY_OPERATOR_KEY__", html)
            self.assertFalse((Path(tmp) / OPERATOR_KEY_FILENAME).exists(), "the dashboard must never create it")


if __name__ == "__main__":
    unittest.main()
