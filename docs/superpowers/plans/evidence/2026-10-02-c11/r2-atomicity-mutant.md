# R2 atomicity: the forget takes the write lock before its governing check

Review of 1f50175b: the governing SELECT ran before any write lock, so a definition push committing
between the check and the DELETE was forgotten with its environment.

Fix: `control_environment` issues `BEGIN IMMEDIATE` before reading the environment row when the action
is forget; the 404 and the 409 roll back; the DML commits in the same transaction.

Test: `test_A_PUSH_THAT_COMMITS_WHILE_A_FORGET_WAITS_IS_SEEN`. A second connection holds the write lock,
the forget is shown waiting (control), the holder commits a store row and one definition, and the
forget must answer 409 naming `1 from <machine>`.

Mutant (2026-10-02): `BEGIN IMMEDIATE` replaced by `SELECT 1` in service/routers/environments.py.
- mutant: `AssertionError: 200 != 409 : {"ok":true,"action":"forget",...}`, 1 failed, 10 passed
- restored (cp, cmp clean): 11 passed
The waiting control held under the mutant too (the forget blocked at its DELETE), so the red is the
ordering, not a test that stopped waiting.
