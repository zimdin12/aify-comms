r"""Every configuration knob does something.

A field on `ServiceConfig` is settable from two places -- an environment variable in the `env_map`,
and `service.json`, whose loader sets ANY key matching an attribute name. If nothing reads it, the
operator sets a value and the service does not change. That is worse than an absent knob: an absent
one is obviously absent, and a dead one looks like control.

WHAT THIS WAS WRITTEN FROM, measured 2026-08-29: 22 fields declared, 20 environment variables
accepted, and THREE fields read by nothing outside `config.py` -- `host`, `mcp_user_id` and
`mcp_app_name`.

`host` is the one that mattered. It reads like the way to bind the service to loopback, and the
container never asks it::

    Dockerfile:58  CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", ...]

so `HOST=127.0.0.1` in `.env` changed nothing and said nothing. The real lever is the compose port
mapping, which is what `aify-comms doctor`'s `api-exposure` check already names in its fix text --
"publish the port on 127.0.0.1 instead of 0.0.0.0". The check was right; the knob beside it was the
dead one. All three are gone, and this is what stops the next one arriving.

SCOPE. A field is "read" if any module outside `config.py` names it on a config object. A field read
only through a dict or a `getattr` with a computed name would not be seen -- and none is, today: the
one generic path is `service.json`'s loader, which WRITES.
"""
from __future__ import annotations

import ast
import re
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"
CONFIG_PY = SERVICE / "config.py"

#: `config.x`, `cfg.x`, `get_config().x` -- a read through a name that is obviously the config.
READ = re.compile(r"\b(?:config|cfg|settings|_config|get_config\(\))\.([a-z_]+)\b")

#: Fields that are legitimately write-only, each with the reason it is not a dead knob.
#: EMPTY, and that is the point: the three that were here are deleted rather than exempted.
WRITE_ONLY: dict[str, str] = {}


def declared_fields() -> list[str]:
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ServiceConfig":
            return [t.target.id for t in node.body
                    if isinstance(t, ast.AnnAssign) and isinstance(t.target, ast.Name)]
    raise AssertionError("ServiceConfig was not found; this gate is reading the wrong file")


def field_reads(fields: list[str]) -> dict[str, set[str]]:
    hits: dict[str, set[str]] = defaultdict(set)
    for path in SERVICE.rglob("*.py"):
        if "__pycache__" in path.parts or path == CONFIG_PY:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for match in READ.finditer(line):
                if match.group(1) in fields:
                    hits[match.group(1)].add(f"{rel}:{number}")
    return hits


def env_map_targets() -> set[str]:
    """The attribute each accepted environment variable writes to."""
    text = CONFIG_PY.read_text(encoding="utf-8")
    block = text[text.index("env_map = {"):text.index("for env_key, target in env_map")]
    plain = set(re.findall(r':\s*"([a-z_]+)"', block))
    tupled = set(re.findall(r':\s*\(\s*"([a-z_]+)"', block))
    return plain | tupled


class EveryConfigKnobDoesSomethingTests(unittest.TestCase):
    def test_the_scans_found_their_subject(self) -> None:
        """The control. An empty field list or an empty read map would satisfy everything below."""
        fields = declared_fields()
        self.assertGreaterEqual(len(fields), 15, f"implausibly few config fields: {fields}")
        reads = field_reads(fields)
        self.assertIn("data_dir", reads, "a field certainly read was not seen")
        self.assertGreaterEqual(len(reads), 10, "the read scan found almost nothing")

    def test_the_scan_can_say_no(self) -> None:
        """The negative control: a name that is not a field must not be reported as read."""
        self.assertNotIn("aify_not_a_config_field", field_reads(declared_fields()))
        self.assertNotIn("aify_not_a_config_field", declared_fields())

    def test_no_declared_field_is_read_by_nothing(self) -> None:
        fields = declared_fields()
        reads = field_reads(fields)
        dead = sorted(f for f in fields if f not in reads and f not in WRITE_ONLY)
        self.assertEqual(dead, [], (
            "these config fields are settable from .env and service.json and read by nothing, so an "
            "operator setting one gets no change and no warning: " + ", ".join(dead)
        ))

    def test_every_environment_variable_writes_to_a_field_that_exists(self) -> None:
        """The other direction. An env var whose target was renamed sets an attribute on nothing."""
        fields = set(declared_fields())
        orphans = sorted(t for t in env_map_targets() if t not in fields)
        self.assertEqual(orphans, [], (
            "the env map writes to attributes ServiceConfig does not declare: " + ", ".join(orphans)
        ))

    def test_an_exemption_must_carry_its_reason(self) -> None:
        """`WRITE_ONLY` is empty on purpose. If a future field earns a place there it must say why --
        an exemption with no argument is how a dead knob becomes permanent."""
        for name, reason in WRITE_ONLY.items():
            self.assertGreater(len(reason.split()), 5, f"{name}'s exemption has no argument")


if __name__ == "__main__":
    unittest.main()
