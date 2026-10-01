# P2 evidence: plugins follow the registry (P0 C8)

What was run against aify-env `next/env-owned-agents` at **d2ce11f**: P2 as approved at 495524c, plus the
split of the aify-comms plugin (below). The tree tested is the tree committed: nothing changed between
the runs below and the commit. Earlier rounds' files (c1a4596, 495524c) are in git history.

| file | what it is |
|---|---|
| `mutations.json` | 48 mutations: each rule of C8, the R1 repair, and the split's parts, broken on purpose |
| `mutate.py` | P1's driver, plus the P2 test set and a timeout that kills the whole process tree, reporting `HUNG` |
| `mutations-result.txt` | the run: 48/48 killed, each with the tests that killed it |
| `aify-env-full-suite-verdicts.txt` | `npm test` in aify-env: every verdict line and the totals (2272 tests, 2268 pass, 4 skipped, 0 fail) |
| `other-suites.txt` | the aify-comms and aify-wrapper suites with `AIFY_ENV_REPO` pointed at the branch |

The driver's hang path was proven before this run on a probe whose test hangs through a node child:
`HUNG` in 6 s, a failing test killed and a passing one survived, the target restored, and no process
left. That leftover-process count was itself controlled by starting one such process and seeing it
counted.

## The survivor, and why my first account of it was wrong

In the first round, "the claim loop keeps claiming while held" survived, and I wrote that it was
equivalent and that no test could tell it apart. The review of c1a4596 showed otherwise. The mutant
claims nothing, but its claim loop keeps beginning setups while held. A setup in flight then blocks the
final detach until that setup is released. With the control long-poll open, the correct code begins no
setup at all.

The held witness now counts workspace-root reads. It first checks, while running, that the count can
move, then requires it to stay still while held with the long-poll open. It goes red on the mutant, on
that assertion.

## Review round 1 (c1a4596): the held plugin's key

R1, from the review: a held plugin kept its old endpoint but took its key from the registry's
current first target (`advertisingTargets[0]`). After a repoint it sent the new service's key to the
old endpoint, and after a removal it sent no key. The resolver predates P2; P2's held state is what
exposed it.

The fix: each plugin carries the registry entry it was built from (`pluginsForServices` passes it, and
the plugin asks `host.credential(entry)`). The daemon resolves that entry fresh on every request through
`pluginCredential`. Rotation still reaches a plugin, a held plugin keeps its old binding, and no entry
means no key, never another service's.

Witnesses are in `tests/a-held-plugin-keeps-its-own-key.test.js`. They run the real chain
(`pluginsForServices`, the plugin, `CommsApi`, `PluginHost`, `pluginCredential`, `credentialForTarget`),
with only `fetch` replaced and synthetic keys:

- repoint, removal and rotation, with an unrelated service first in the registry throughout;
- a planted resolver that reads the current registry, which the same observation catches;
- the daemon's own resolver and host property, taken out of `bin/aify-env.mjs` and run.

## The split (after P2's approval at 495524c)

`index.mjs` was 507 lines, past the 400-line signal. Three parts left it:

- `plugin-phase.mjs`: the class `PluginPhase`, which owns C8's phase, the passes in flight and the
  moves between them.
- `pass-loop.mjs`: `runPasses`. Both loops carried their own copy of the skeleton, and of the lesson
  behind it: a throwing pass costs one interval, never the loop.
- `claimer-answer.mjs`: `claimerFromAnswer`, a pure function. The answer comes in; the claimer state
  and the line to log come out.

`index.mjs` keeps the wiring and is now 424 lines. Its behaviour is unchanged: the 42 earlier mutations
were retargeted to where each rule now lives, and all still die on the same witnesses.

Six new mutations break the parts themselves. Each is killed by `aify-comms-plugin-parts.test.js` and
also by an existing plugin-level test, so the parts tests overlap the plugin's. They add a direct
statement of each part's contract: the phase transitions, and that `quiesce` waits for a tracked pass.

## What the tests found

- **A pass could start after the detach had decided.** A pass's setup (`advertisement()`,
  `cwdRoots()`) ran before `controlPass` was recorded, so a detach landing in it waited for nothing.
  The pass is now tracked whole and re-reads the phase. Witness: "A PASS WHOSE SETUP OUTLASTS THE
  DETACH".
- **A reverted registry change left the plugin held for ever.** Nothing asked it to detach again, and
  nothing un-held it, so it refused starts until a daemon restart. `resume()` was added.
- **The doctor read a held plugin as "claiming work".** Its claimer is still accepted, and it starts
  nothing. The `claiming` row now fails with "registry change pending".
- **The 0.8 branch still carried the withdrawn line-feed translation.** It forked after those commits.
  Main's revert is merged at 998b2e2.
- **P1's evidence failed this repo's whitespace gate**: its `mutations.json` lacked a final newline,
  and the RED transcript had 4 bytes of trailing spaces. Both are fixed, and `git diff -w` on the
  transcript is empty.

A mutant can kill by hanging. The first run had no hang budget: "detach sends the offline beat" waited
for ever on a held heartbeat, and the 30-minute background limit stopped the run with that file still
mutated. It was restored from the driver's own backup and checked byte for byte. Every witness now has
a 10 s timeout.

In the review round, that was not enough, for two reasons:

- A test file whose plugins keep running never exits, even when every test in it has timed out.
  Per-test timeouts do not bound the cleanup hooks or the event loop. Each file now stops every
  plugin it started, the ones `followRegistry` started included.
- `subprocess.run(..., timeout=)` with a shell kills only the shell on Windows. The node child keeps
  the pipe open, and the read after the kill blocked: one mutant sat 1054 s past a 300 s timeout.
  The driver now kills the process tree.

## What this does not check

- **The daemon's follow wiring** (`followServices` with its `following ??=` single flight and its
  `logLine` reporting) is not executed by any test. Only its guard statement is, taken out of
  `bin/aify-env.mjs` and run. That this statement runs on every advertiser beat is read from the
  source, not tested.
- **Nothing here ran against a real service or a real registry edit.** Every result above is PASSES
  IN TESTS: the real aify-comms plugin with a fake transport, or with only `fetch` replaced, and fake
  processes.
- **`aify-comms`'s Python suite** has one failure, the version gate. It is red until HEAD is tagged
  (VERSION is 0.7.7 on a tree past `v0.7.7`) and closes at P7's bump to 0.8.0. One file was
  deselected: the P3 role-reset test, which is uncommitted and red on purpose.
- **The daemon's `following ??=` closure** was run by the reviewer in a vm (overlap single-flights,
  success and rejection both report, and a later beat runs). It drops overlapping registry reads and
  relies on the next beat. That is the reviewer's evidence, not a test in this repo.

To rerun: `python mutate.py <aify-env checkout> mutations.json`.
