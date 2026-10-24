"""One install table, copied into seven files, corrected in three of them.

THE DEFECT, measured 2026-09-02. The "how do I install this" table appears in `README.md` and in
every `install.<runtime>.md`. Each copy said to get aify-env with `npm install -g` -- a command that
installs the binary and CANNOT notice a missing service credential. A host installed that way
advertises, is refused with 401, and reports healthy throughout; that is exactly the failure that
cost a day, and the instruction to reproduce it was sitting in seven places.

Correcting it meant editing every copy, and the copy that rots is always the one whose author was not
looking at it. This repo has met that shape repeatedly: two different dashboard test counts written
on the same day, a layout table stale three times, wrapper templates duplicated until a hash gate
was added.

SO THE RULE IS AGREEMENT, NOT SINGLE-SOURCING. The table cannot be single-sourced -- these are
standalone guides a reader may arrive at directly, and a pointer to another file is worse for them
than a duplicated row. What CAN be enforced is that the copies say the same thing, which is what
this checks.

IT ASSERTS A PROPERTY, NOT A STRING. Pinning the exact row would fail on every wording change and
would be edited to match rather than obeyed -- a gate nobody trusts is a gate that gets updated
thoughtlessly. What matters is that no guide still tells a reader to install aify-env by a route
that skips its credential.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Every file carrying an install instruction a reader might follow directly.
GUIDES = ["README.md"] + [p.name for p in sorted(REPO.glob("install.*.md"))]

#: The route that cannot ask for a credential. Naming the SHAPE rather than one string, because the
#: package spec has already been written three ways (bare name, git form, pinned sha).
NPM_GLOBAL_AIFY_ENV = re.compile(r"npm\s+install\s+-g\s+\S*aify-env", re.I)

#: What a guide should point at instead.
INSTALLER = re.compile(r"install\.sh", re.I)


# Matches offered shell commands in fences, inline table cells, installer help and source examples.
# Discussion of a flag alone is not a command; a trailing comment cannot grant standalone mode.
INSTALL_COMMAND = re.compile(r'(?:bash\s+install\.sh|\.\\install\.cmd)\s+--(?:client|standalone)\b[^`"\n]*')


def uncarried_hook_commands(text: str) -> list[str]:
    """Commands that ask the guided chain for the legacy hook it refuses with exit64.

    Runtime refusal is covered by the bridge's plain-install handoff test; this checks its readers.
    """
    return [
        command for match in INSTALL_COMMAND.finditer(text)
        if (command := match.group().split('#', 1)[0].strip())
        and '--with-hook' in (flags := set(re.findall(r'--[a-z][a-z0-9-]*', command)))
        and '--standalone' not in flags
    ]


class TheInstallTableAgreesTests(unittest.TestCase):
    def test_the_hook_command_scan_distinguishes_default_chain_and_standalone(self):
        for command in (
            'bash install.sh --client claude http://host:8800 --with-hook',
            r'.\install.cmd --client claude http://host:8800 --with-hook',
        ):
            self.assertEqual(uncarried_hook_commands(command), [command])
            self.assertEqual(uncarried_hook_commands(f'| `{command}` | client install |'), [command])
            self.assertEqual(uncarried_hook_commands(command + ' # --standalone'), [command])
            self.assertEqual(uncarried_hook_commands(command + ' --standalone'), [])
        self.assertEqual(uncarried_hook_commands('bash install.sh --client claude http://host:8800'), [])
        self.assertEqual(uncarried_hook_commands('bash install.sh --standalone --client claude --with-hook'), [])
        self.assertEqual(uncarried_hook_commands('Use `--standalone --with-hook` for the legacy hook.'), [])

    def test_no_guide_offers_a_hook_the_default_chain_refuses(self):
        """A copied quickstart or help example must not recreate the measured exit64."""
        offenders = {
            name: commands for name in [*GUIDES, 'install.sh']
            if (commands := uncarried_hook_commands(self._text(name)))
        }
        self.assertEqual(offenders, {},
                         'these hook commands need explicit --standalone, not the default chain')

    def test_codex_examples_keep_their_unread_message_hook(self):
        """The guide, help and header must retain the Codex unread-discovery opt-in."""
        commands = [
            (name, command) for name in [*GUIDES, 'install.sh']
            for command in INSTALL_COMMAND.findall(self._text(name))
            if re.search(r'--client\s+codex\b', command)
        ]
        self.assertTrue(commands, 'the install guides must offer a concrete Codex command')
        for name, command in commands:
            with self.subTest(document=name, command=command):
                self.assertIn('--standalone', command, 'the guided chain does not carry the hook yet')
                self.assertIn('--with-hook', command, 'Codex examples must install unread-mail discovery')

    def _text(self, name: str) -> str:
        return (REPO / name).read_text(encoding="utf-8", errors="replace")

    def test_the_guides_exist_and_were_found(self):
        """CONTROL. A glob that matched nothing would make every assertion below vacuous, which is
        this repo's most repeated failure: a zero that agrees with what you expected raises no
        collision, so nothing prompts you to check the instrument."""
        self.assertGreaterEqual(len(GUIDES), 4, f"only found {GUIDES}")
        self.assertIn("README.md", GUIDES)
        self.assertTrue(any(g.startswith("install.") for g in GUIDES))

    def test_no_guide_tells_a_reader_to_install_aify_env_without_its_installer(self):
        """THE DEFECT. `npm install -g` leaves a host with no credential, and nothing asks -- so it
        advertises, is refused with 401, and reports healthy while no spawn can be claimed."""
        offenders = []
        for name in GUIDES:
            text = self._text(name)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not NPM_GLOBAL_AIFY_ENV.search(line):
                    continue
                # A line that names the command in order to say it is INSUFFICIENT is the correction,
                # not the defect. The distinguishing mark is that it also names the installer.
                if INSTALLER.search(line) or "cannot notice" in line or "still installs" in line:
                    continue
                offenders.append(f"{name}:{line_number}: {line.strip()[:95]}")
        self.assertEqual(
            offenders, [],
            "a guide still routes a reader around aify-env's installer. That command installs the "
            "binary and cannot notice a missing service credential, which is the 2026-09-02 failure "
            "verbatim. Correct EVERY copy: the one that rots is the one nobody was looking at.",
        )

    def test_every_guide_that_mentions_aify_env_names_its_installer(self):
        """The positive half. Refusing the wrong route is not the same as giving the right one, and a
        reader who is only told what NOT to do improvises."""
        missing = []
        for name in GUIDES:
            text = self._text(name)
            if "aify-env" not in text:
                continue
            if not INSTALLER.search(text):
                missing.append(name)
        self.assertEqual(missing, [],
                         "these name aify-env without naming how to install it")

    def test_the_check_can_actually_fail(self):
        """MUTATION, in-line: the matcher must recognise the defect it exists to catch. Without this
        a broken regex passes every file silently -- and a regex that matched nothing would look
        exactly like a clean repository."""
        self.assertTrue(NPM_GLOBAL_AIFY_ENV.search("npm install -g github:zimdin12/aify-env"))
        self.assertTrue(NPM_GLOBAL_AIFY_ENV.search("  npm install -g aify-env && aify-env"))
        self.assertIsNone(NPM_GLOBAL_AIFY_ENV.search("npm install -g aify-wrapper"),
                          "the matcher fires on an unrelated package")


if __name__ == "__main__":
    unittest.main()
