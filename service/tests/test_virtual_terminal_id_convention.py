"""Fourteen queries decide "is this a REAL PTY?" from a string prefix. Nothing checked the prefix.

A terminal row is either a real wrapper PTY or a virtual RPC console (pi/codex app-server), and the
whole codebase tells them apart with one SQL idiom:

    AND id NOT LIKE 'vterm_%'

Fourteen sites use it, across liveness, worker-presence, managed-worker reconciling, session-handle
changes and dead-session status. What they feed is `worker_present` — which decides an agent's
status, which decides whether a dispatch is routed, queued or refused. A virtual console counted as a
real worker is an agent that looks alive with nothing to run its turn.

The convention that makes those queries correct lives nowhere except in the four places that MINT an
id, as inline f-strings:

    virtual_terminal.py      vterm_...   virtual
    console_terminal_rows.py vterm_...   virtual
    managed_pty_for_dispatch.py term_... real
    session_console.py          term_... real

A fifth mint that ignores it does not fail — it silently misclassifies at every one of the fourteen
sites at once. `test_stop_worker_stops_real_terminals.py` covers ONE consumer's bug; nothing covered
the convention.

THE TRAP, verified against sqlite rather than reasoned about: `virtual_...` is classified REAL. The
same module that mints `vterm_` also builds the string `virtual_{runtime}_rpc_attached` a few lines
below, so the near-miss prefix is already in the file. So is the fact that `_` is a LIKE WILDCARD,
not a literal — `vtermX...` and `vterm2_...` both read as virtual, which is the harmless direction,
but it means the idiom is looser than it looks.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sqlite3
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PRUNE = {"node_modules", "fixtures", "__pycache__", ".git", ".venv", "tests"}

VIRTUAL_PREFIX = "vterm_"
REAL_PREFIX = "term_"
CLASSIFIER = "vterm_%"

#: Files that create VIRTUAL (RPC-backed) terminal rows. Everything else minting a terminal id is
#: creating a real PTY. Frozen: a new virtual-console path must be added here deliberately.
VIRTUAL_OWNERS = {
    "service/routers/agents/virtual_terminal.py",
    "service/api_core/console_terminal_rows.py",
}
#: Every site that mints a terminal id today, and which prefix it must use.
EXPECTED_MINTS = {
    "service/routers/agents/virtual_terminal.py": VIRTUAL_PREFIX,
    "service/api_core/console_terminal_rows.py": VIRTUAL_PREFIX,
    "service/api_core/managed_pty_for_dispatch.py": REAL_PREFIX,
    "service/routers/session_console.py": REAL_PREFIX,
}

MINT = re.compile(r'terminal_id\s*=\s*f"([a-z_]+?_)\{')


def _sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted((REPO / "service").rglob("*.py")):
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def _mints() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for rel, src in _sources():
        for match in MINT.finditer(src):
            found.setdefault(rel, set()).add(match.group(1))
    return found


def _classifier_sites() -> list[str]:
    return [
        f"{rel}:{i + 1}"
        for rel, src in _sources()
        for i, line in enumerate(src.splitlines())
        if CLASSIFIER in line
    ]


class VirtualTerminalIdConventionTests(unittest.TestCase):
    # ── the classifier's real semantics, measured not assumed ────────────────────────────────

    def test_the_like_idiom_separates_the_two_kinds(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE terminal_sessions (id TEXT)")
        rows = {
            "term_1_a": True,        # real PTY
            "vterm_1_a": False,      # virtual RPC console
            "virtual_1_a": True,     # THE TRAP: near-miss prefix reads as REAL
            "vtermX1_a": False,      # `_` is a LIKE wildcard, so this reads as virtual too
        }
        db.executemany("INSERT INTO terminal_sessions VALUES (?)", [(k,) for k in rows])
        real = {
            r[0] for r in db.execute(
                f"SELECT id FROM terminal_sessions WHERE id NOT LIKE '{CLASSIFIER}'")
        }
        for terminal_id, expected_real in rows.items():
            with self.subTest(terminal_id=terminal_id):
                self.assertEqual(
                    terminal_id in real, expected_real,
                    f"{terminal_id} classified as {'real' if terminal_id in real else 'virtual'}",
                )

    # ── every mint honours the convention ────────────────────────────────────────────────────

    def test_every_terminal_id_mint_uses_a_known_prefix(self):
        actual = {rel: sorted(prefixes) for rel, prefixes in _mints().items()}
        expected = {rel: [prefix] for rel, prefix in EXPECTED_MINTS.items()}
        self.assertEqual(
            actual, expected,
            "the set of terminal-id mints changed. A new one must be classified HERE: a virtual "
            f"(RPC-backed) console mints {VIRTUAL_PREFIX!r}, a real PTY mints {REAL_PREFIX!r}. Any "
            f"other prefix — {'virtual_'!r} in particular, which is already used a few lines from "
            "one mint — is read as a REAL terminal by every query below and counts as a live "
            "worker.",
        )

    def test_the_virtual_owners_mint_the_virtual_prefix(self):
        mints = _mints()
        for owner in VIRTUAL_OWNERS:
            with self.subTest(owner=owner):
                self.assertEqual(
                    mints.get(owner), {VIRTUAL_PREFIX},
                    f"{owner} creates virtual RPC consoles and must mint {VIRTUAL_PREFIX!r}",
                )

    def test_no_real_pty_mint_uses_the_virtual_prefix(self):
        mints = _mints()
        for rel, prefixes in mints.items():
            if rel in VIRTUAL_OWNERS:
                continue
            with self.subTest(rel=rel):
                self.assertNotIn(
                    VIRTUAL_PREFIX, prefixes,
                    f"{rel} mints a real PTY as {VIRTUAL_PREFIX!r}, so liveness and worker-presence "
                    "will skip it and the agent will read as having no worker",
                )

    # ── the population that depends on it ────────────────────────────────────────────────────

    def test_the_classifier_is_used_widely_enough_to_matter(self):
        """Not a count to maintain — a floor. If this collapses, either the idiom was replaced (and
        this file should be too) or the classification quietly stopped happening."""
        sites = _classifier_sites()
        self.assertGreaterEqual(
            len(sites), 10,
            f"only {len(sites)} sites classify on {CLASSIFIER!r}; the convention may have been "
            f"replaced without this test being updated: {sites}",
        )

    # ── anti-vacuity ─────────────────────────────────────────────────────────────────────────

    def test_the_mint_scanner_actually_finds_things(self):
        """Every assertion above passes trivially if the regex matches nothing."""
        self.assertGreaterEqual(len(_mints()), 4)
        self.assertEqual(
            MINT.findall('    terminal_id = f"vterm_{int(time.time())}"'), ["vterm_"],
        )
        self.assertEqual(MINT.findall('terminal_id = existing_row["id"]'), [])
