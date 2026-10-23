# --report-file outcome (S2 review of wrapper 0e05ed8)

aify-wrapper `cc66459` (on `0e05ed8`): `--report-file` is checked before connect publishes anything (exists, or its
directory is missing or not a directory: exit 78, nothing published). A record that cannot be written AFTER a commit is
a stderr warning; connect still exits 0 committed, and a caller that finds no record treats it as nothing installed.

Commands, in the aify-wrapper worktree:
- `red.txt`: the old CLI (`git show 0e05ed8:bin/aify-wrapper-connect.mjs`) with the new lib and tests,
  `npm test -- tests/connect-package.test.js`: 9 pass, 2 fail (the pre-publish refusal; the post-commit outcome).
- `green.txt`: the fixed CLI restored from a byte copy (`cmp` equal): 11 pass.
- `mutant-rethrow.txt`: `deliverConnectRecord` rethrows: tests 5 and 6 fail. Restored by byte copy (`cmp` equal).
- `full-1.txt`: full `npm test` before naming `connectRecord`/`writeConnectRecord` in a test: 2 fail. The
  every-export gate also fails on `0e05ed8`. The second failure is the codex `--shared --resume` test, which passed alone
  in `focused2.txt` and is not attributed.
- `full-2.txt`: full `npm test` at `cc66459`: 912 tests, 842 pass, 0 fail, 70 skipped, exit 0.

The post-commit test uses a 300-character file name, which no filesystem accepts, so its write fails after the checks.
