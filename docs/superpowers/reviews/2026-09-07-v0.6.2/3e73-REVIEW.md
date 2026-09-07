# Redirect correction and regression gate review

Verdict: REVISE for the claimed gate. The two reproduced runtime-client redirect leaks narrowly close on the unmodified source.

Subject: 3e7387a6f6cf8a001c0f8372e9a70ff1b96244c2.
Predecessor: b28a23e41f9a5b5c0b7e688409308509508843e5.
Reply contract: 1788821983138-3be79b47.
Checkout: C:/Users/Administrator/AppData/Local/Temp/aify-v062-review/3e73-source.
Evidence directory: C:/Users/Administrator/AppData/Local/Temp/aify-v062-review.

## Verified closure

The actual exported httpCall and makeAifyHttpCall were imported in sealed child environments with a disposable home, a synthetic registry-owned credential written by the frozen env secure writer, and two local HTTP receivers. No live credential was read by these probes.

For BOTH clients, an ordinary 200 succeeds and the authorized receiver sees the synthetic key. For each of 301, 302, 303, 307 and 308, the unmodified client throws the corresponding HTTP status and the unrelated receiver gets no request. This is 12 executed GET cases, not source-only or stubbed-fetch evidence. It narrowly closes the previously reproduced two-client redirect P1. Other modified request owners were inspected in the exact diff but were not individually receiver-tested here.

The requested native reply succeeded. comms_run_status(run_1788822017420_8c967070) records claimed, delivered and completed by the channel bridge. That establishes this session's native outbound path after the reported restart, not recipient reasoning, fleet-wide byte convergence, certificate custody, or every installed client's identity.

## P2: The gate accepts a leaking call when policy text is present but ineffective

mcp/stdio/tests/a-request-carrying-the-key-never-follows-a-redirect.test.js:39,63-67 counts regular-expression hits over whole file text. It does not associate executable policy with a specific fetch or evaluate the effective options. Both following mutations to the real call in aify-service-endpoint.mjs:283 pass all three committed gate tests while restoring key forwarding to the unrelated receiver for every tested redirect status:

```js
const res = await fetch(url, { ...options /* redirect: "manual" */ });
```

```js
const res = await fetch(url, { ...options, redirect: "manual", ...{ redirect: "follow" } });
```

These are separate scratch arms. The first has no executable policy; the second has an explicit later override. The gate's isolated negative literal test at :56 passes in both broken trees. Whole-file equal counts do not prove per-call coverage.

A cause-specific control deletes the policy without preserving its text. That produces exit 1 naming aify-service-endpoint.mjs, and the actual client again leaks. Therefore the false greens are not an unexecuted test or a broken runner. Literal deletion is detected; semantically equivalent regressions can survive.

## P2: Header spelling silently removes a real HTTP owner from the population

The same test at :30-33 only enumerates immediate .js/.mjs files; :37 and :62 use a case-sensitive X-API-Key source predicate. The positive control at :45-48 asserts a minimum number of files and one named owner, not the complete population.

In aify-http.mjs, change the X-API-Key spelling to x-api-key throughout that file and remove its redirect property at :63. HTTP header semantics are unchanged by the case change. All three committed gate tests still pass, yet the actual standalone client sends the synthetic key to the unrelated receiver for 301/302/303/307/308. This is an executed population-exclusion witness, not only a theoretical directory/alias concern.

The nonrecursive walk and exact fetch( spelling are additional source-visible coverage limits, not separately executed current-production leaks. No claim is made that the restored candidate currently contains one of these regressions.

## Smallest useful repair

Keep the runtime policies. Replace the file-wide text-count assertion with per-call executable checks and receiver tests. Derive the declared recursive production-source population independently of a case-sensitive header spelling. Governing all production fetch calls avoids making a header-taint approximation into authority. If a narrower population is retained, unclassified header/call/value flows must remain explicit UNKNOWN or fail closed, not silently disappear.

Associate policy with the actual request options and reject unsupported or overriding forms. Keep the comment-only, later-follow override, and lowercase-header omission mutants as regression controls. Supplement syntactic checks with authenticated positive receivers and redirect refusal through production request owners. A direct literal count, an AST node's mere presence, or one global safe-policy helper does not prove that every call consumes the safe value.

## Independent execution and scope

- Committed gate baseline: 3/3 pass.
- Literal deletion arm: gate exit 1, expected file named; runtime leak witnessed.
- Comment-only arm: gate 3/3 pass; runtime leak witnessed.
- Later-follow spread arm: gate 3/3 pass; runtime leak witnessed.
- Lowercase-header plus omission arm: gate 3/3 pass; runtime leak witnessed.
- Restored gate: 3/3 pass.
- Six-file focused suite: 65 tests pass, zero failures/skips, exit 0.
- An earlier focused-suite attempt lacked aify-wrapper in the detached worktree. That exit 1 is retained as setup-invalid evidence, not a source failure. The successful rerun used a junction to the existing C:/Docker/aify-comms/mcp/stdio/node_modules without installing or updating packages.
- Node v22.20.0. Exact range git diff --check exits 0. The detached checkout has empty git status after restoring every mutant.
- The diff adds 17 executable manual-policy lines across 10 production files, not the packet's eleven/eight count. See 3e73-added-policy-census.json. This count is a diff census, not a claim that all added policies carry a credential.

Key evidence: 3e73-redirect-probe.mjs; 3e73-gate-attacks.py; 3e73-mutation-results.json; 3e73-baseline-runtime.log; the per-arm gate/runtime logs; 3e73-focus.log; 3e73-focus-setup-invalid.log.

I agree with R8 first among the listed remaining behavior fixes, including painting that begins after attachment and preserving escape. The acknowledged doctor identity/byte fidelity, resize-test, width, docs and coherent console-cut debts stay open. The full author-reported five suites, fleet restarts/boot hashes, trust-store operation and global CLI upgrade were not independently repeated. No production edit, installation/update, deployment, restart, process reaping or tag action was performed by this review.
