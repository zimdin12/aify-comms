# 0.9 P1: install.sh gives hermes the session servers (aify-wrapper 764d961, merged at 686d182)

`scripts/hermes-session-servers.sh` runs the pinned aify-wrapper `lib/hermes-config-cli.mjs`; install.sh calls it with
`--check` at the top of `install_hermes_wrapper` (before the launcher is rendered, also under `--emit-wrappers`) and
without it right after `install_hermes_wrapper` on the real install path. install.sh stays at its 2260 ceiling: the
two calls cost 5 lines, paid by my two comment lines and a 4-line comment cut to 1 beside the hermes delivery echo.

Test: `mcp/stdio/tests/hermes-session-servers.test.js` (5 cases), driven through the pinned package and aify-wrapper's
own `tests/fake-hermes.mjs` (sibling checkout), every hermes root sealed to a temp home.

Mutants, 2026-10-02, each restored by cp and checked with cmp:
- M1 drop the `--check` call from install.sh: case 5 red (a refusing registry still wrote hermes-aify).
- M2 the script ignores `--check` (always writes): case 3 red only after its control was tightened to assert a
  givable registry under `--check` runs no hermes and writes nothing. The first version survived M2, because a
  refusal stops before hermes in both modes and so cannot tell a check from a write.
- M3 a missing tool exits 0: case 4 red.
- restored: 5 passed.

NOT covered: the write call site on the real install path. Running it would run the real hermes on this host; the
call is read, not exercised.
