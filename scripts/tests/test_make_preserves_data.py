"""Read effective Make recipes without starting Docker or deleting anything."""
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_compose_teardown_in_make_preserves_named_volumes():
    make = shutil.which('make') or shutil.which('mingw32-make')
    if not make:
        pytest.skip('GNU Make is not available')
    for target, options in [('down', []), ('clean', ['--remove-orphans'])]:
        command = subprocess.run([make, '--no-print-directory', '-n', target], cwd=ROOT,
                                 capture_output=True, text=True, check=True)
        assert shlex.split(command.stdout) == ['docker', 'compose', 'down', *options], target
