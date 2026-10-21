"""Mutation arms for the gate's primary window (SW3a review of 11d8f3ae: runner R2 was not consumed by gate attempts),
run by scripts/sandbox/gate/campaign.py:

  python scripts/sandbox/gate/campaign.py <worktree> <out> docs/evidence/2026-10-21-sw3a-gate/arms/run_arms.py       docs/evidence/2026-10-21-sw3a-gate/arms/section6_arms.py docs/evidence/2026-10-21-sw3a-gate/window/window_arms.py

It names a checker for gate.mjs only: section6_arms.py supplies '*', which a second '*' here would replace.

The runner's half (cleanup lifts the window itself) lives outside the gate directory the campaign mutates; its arm is
run by hand and kept in runner-cleanup-arm.txt. Read as literals, never executed."""

CHECKERS = {
    'gate.mjs': ['node', '--test', 'scripts/tests/gate_attempt_window.mjs'],
}

# (arm, file, the span replaced, its replacement, the witness that must be named, the obligation it guards)
ARMS = [
  ('w01', 'gate.mjs', '  setPrimaryUntil(until);\n', '',
   'the window cuts a command running when the attempt budget passes', 'R2 admission and cap in a gate attempt'),
  ('w02', 'gate.mjs', '    deadline(); // a verdict reached after the budget is not one\n', '',
   'a verdict reached after the attempt budget is refused', 'terminal check before the verdict and attempt.json'),
]
