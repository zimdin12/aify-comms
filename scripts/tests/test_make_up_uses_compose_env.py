"""Exercise real Make and setup; the Docker executable is only a local argv recorder."""
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
START = 'bash scripts/stamp.sh && docker compose up -d --build'


def write_program(path, script):
    path.write_text('#!/bin/sh\nset -eu\n' + script, encoding='utf-8', newline='\n')
    path.chmod(0o755)


@unittest.skipUnless(os.name != 'nt' and shutil.which('make'), 'requires POSIX GNU Make')
class MakeUpUsesComposeEnv(unittest.TestCase):
    def test_fresh_setup_reaches_compose_after_stamping_without_shell_parsing(self):
        for line_end in ('\n', '\r\n'):
            with self.subTest(crlf=line_end == '\r\n'), TemporaryDirectory() as tmp:
                project = Path(tmp)
                for folder in ('scripts', 'config', 'bin'):
                    (project / folder).mkdir()
                for file in ('Makefile', '.env.example', 'setup.sh', 'config/service.example.json',
                             'docker-compose.override.yml.example'):
                    shutil.copyfile(ROOT / file, project / file)
                wrapper = ROOT / 'scripts/compose-up.sh'
                if wrapper.is_file():
                    shutil.copyfile(wrapper, project / 'scripts' / wrapper.name)
                write_program(project / 'scripts/stamp.sh', 'printf "stamped\\n" > calls\n')
                write_program(project / 'bin/docker', 'test -f calls\nprintf "%s\\n" "$@" >> calls\n')
                env = {'PATH': f'{project / "bin"}:/usr/bin:/bin', 'HOME': str(project)}
                setup = subprocess.run(['bash', 'setup.sh'], cwd=project, env=env,
                                       text=True, capture_output=True, check=True)
                self.assertEqual((project / '.env').read_bytes(), (ROOT / '.env.example').read_bytes())
                dotenv = (project / '.env').read_text(encoding='utf-8')
                # An unsafe source would execute this harmless sentinel, only inside the fixture.
                dotenv += '\nFIXTURE=$(printf evaluated > parsed-as-shell)\n'
                (project / '.env').write_text(dotenv, encoding='utf-8', newline=line_end)
                started = subprocess.run(['make', '--no-print-directory', 'up'], cwd=project, env=env,
                                         text=True, capture_output=True)
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                self.assertEqual((project / 'calls').read_text().splitlines(), ['stamped', 'compose', 'up', '-d', '--build'])
                self.assertFalse((project / 'parsed-as-shell').exists())
                self.assertIn(START, setup.stdout)

    def test_a_failed_stamp_never_reaches_compose(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'scripts').mkdir()
            (project / 'bin').mkdir()
            shutil.copyfile(ROOT / 'Makefile', project / 'Makefile')
            wrapper = ROOT / 'scripts/compose-up.sh'
            if wrapper.is_file():
                shutil.copyfile(wrapper, project / 'scripts' / wrapper.name)
            write_program(project / 'scripts/stamp.sh', 'printf "stamp refused\\n"\nexit 73\n')
            write_program(project / 'bin/docker', 'printf "called\\n" > docker-called\n')
            result = subprocess.run(['make', '--no-print-directory', 'up'], cwd=project,
                                    env={'PATH': f'{project / "bin"}:/usr/bin:/bin', 'HOME': str(project)},
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('stamp refused', result.stdout)
            self.assertFalse((project / 'docker-called').exists())
