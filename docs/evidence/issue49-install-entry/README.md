# Install entry points and approval guidance

2026-11-13. This is the install-entry portion of #49, not a successful fresh install or E1 qualification.

`setup.sh` and `install.sh` are executable in Git. The existing guided handoff prints the selected source and npm
global prefix before installation. The Claude, Hermes and Codex guides explain exact-tuple approval without putting
a key in argv. Codex keeps its standalone hook command; a guided approval does not install that hook.

## Reproduce

```bash
git ls-files --stage -- setup.sh install.sh redeploy.sh
node --test mcp/stdio/tests/a-plain-install-hands-off-to-the-chain-once.test.js
python -m pytest service/tests/test_documented_installs_name_a_source_that_exists.py service/tests/test_prose_paths_resolve.py service/tests/test_docs_name_symbols_not_line_numbers.py service/tests/test_no_doc_teaches_a_removed_command.py service/tests/test_every_doc_is_in_the_index.py service/tests/test_the_install_table_agrees_across_every_guide.py service/tests/test_fixtures_are_tracked.py service/tests/test_no_new_oversized_source_file.py service/tests/test_no_import_cycles.py service/tests/test_no_dead_imports.py service/tests/test_leaves_do_not_import_the_carrier.py -q
```

After commit, `git ls-tree HEAD setup.sh install.sh` must show 100755 for both. The index test uses an existing
executable as a positive control and an absent path as a negative control; it does not infer modes from Windows.

PASSES IN TESTS, under the existing isolated-home runner:

- Mode regression before the fix: exit 1, one failure and three passes. Existing executable and absent controls pass;
  the failing assertion names setup/install as 100644.
- Notice regression before the fix: exit 1, one failure and one existing handoff control passes. The fake npm boundary
  is observed; the missing source/prefix notice causes the intended failure.
- Full handoff file after the fix: exit 0, 21 pass, zero failures/cancellations, one standing non-root POSIX permission
  skip on Windows. The new case checks a source override and prefix with spaces, stderr ordering before fake npm,
  no planted key in the notice, and no install notice when the chain already exists.
- Final selected Python files: exit 0, 55 tests and seven subtests pass.

An initial documentation run failed because the new Codex approval example offered a hookless guided command.
The existing hook-preservation gate caught it. The guide was corrected to explain the approval variable while
retaining its actual standalone hook command; the gate was not weakened. The failed receipt remains retained.

Author receipts are `issue49-entry-modes-red.txt`, `issue49-entry-notice-red.txt`, `issue49-entry-handoff-green.txt`,
`issue49-entry-docs-green.txt` and `issue49-entry-docs-corrected.txt`. npm and chain execution in these tests are
owned fixtures, not a host install. No real global npm write, installer run, provider call or new staging run occurred.
