# S2 preliminary REVISE (1792696220707-f01c8931): two comms defects

1. **A write before a refusal.** install.sh resolved the key (`api-key-for-install.sh`, which can write .env through
   `--generate` or an answered ask) before the handoff refused an option it does not carry. Now the plain path hands
   off first, and the handoff resolves the key itself only after every refusal and aify-env's place are settled, and
   before anything is installed.
2. **An unknown npm root read as "absent".** A failed or empty `npm root -g` became an empty root, and the handoff could
   then install aify-env globally without ruling out a protected link. Now it refuses (78), installing nothing.

| receipt | what |
|---|---|
| `red-before-fix.tap` | the new tests on the predecessor 5ca78a61: 4 fail (npm root unknown went on with exit 1; the plain run resolved before the handoff) |
| `red-ordering-assertion.tap` | the ordering assertion, added after the first red run went red on a control instead: fails on the predecessor naming the order |
| `mutants-attempt1-1a-survived.txt` | first campaign: arm 1a (a resolution inserted ABOVE the handoff) survived, because the test ran only from the handoff line down |
| `mutants.txt` | after the test was widened to run from the installer's main-body header: 3 of 3 killed, each restored byte-equal |
| `green-handoff.tap` | 13 of 13 on the fix |

Focused runs only, as directed: handoff 13/13, register-service-cli-credential 8/8, size ceiling 5/5, and four python
installer test files (49 passed).
