# P6r4: the last H4 case from comms-senior-dev's REVISE of P6r3 (2026-10-02)

The review of comms 92a9eb72 / aify-wrapper 1b53112f approved L1, L2, the .cmd exit status and H3 under the
amended C9, and left one H4 case: a teardown requested while the loop awaits the gateway bring-up found no
effort to stop, and the effort acquired after the bring-up returned set a session anyway.

- Repair: the loop acquires the effort only when no teardown has begun (`teardownState.done`, which
  `makeTeardown` sets on entry). mcp/stdio/hermes-delivery-loop.mjs.
- Test: mcp/stdio/tests/hermes-managed-host.test.js, "a teardown requested during the gateway bring-up means no
  reasoning effort is started": the teardown the loop installs is invoked from inside the bring-up's first
  fetch; nothing is started, and the positive control says the teardown was requested in the bring-up.
- Mutants (`mutations-bridge.json`, `-result.txt`): 2/2 killed. The first restores the reviewed line exactly,
  so it is also the test failing on the reviewed code; the second never acquires, and is killed by the earlier
  lifecycle tests, so ordinary acquisition is still exercised.
