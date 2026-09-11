# Post-v0.6.4 handover — what moved while I was out, and what is still open

**Written 2026-09-11.** I tagged v0.6.4 at `5b55ee3b` on 2026-09-09 and went idle. Another agent
(comms-senior-dev, "dev") worked 2026-09-10 and left seven commits plus six unlanded worktree lanes.
This file is the ANCHOR for the whole-stack review the operator asked for: that review covers
`5b55ee3b..HEAD`, and this is the record of what I knew when I picked the work back up.

## My resume point

- aify-comms `5b55ee3b`, tag `v0.6.4`. Everything after it is dev's, or mine from 2026-09-11 on.
- aify-env `0036b70` (0.6.3), aify-wrapper `aa4e225` (0.6.2). Both clean at handover.

## What dev landed (aify-comms, 7 commits, now tagged v0.6.5)

| commit | what |
|---|---|
| `4a969c25` | native terminal paste sends clipboard text once |
| `b86ce1f1` | truthful hook inventory, optional terminal onboarding |
| `e2a6ef90` | merge: dashboard paste repair + installer onboarding |
| `84a88f04` | reject insecure API overrides on HTTPS dashboards |
| `64cec065` | normalize native installer paths, parse registry inventory |
| `e16f54f6` | HTTPS + upgrade repairs, guided onboarding |
| `a14100a8` | release 0.6.5, honest registration diagnostics |

aify-env also moved `28b20c7` → `0036b70` (0.6.3): Windows Herdr worker attach, plugin heartbeat
source identities, picker readiness.

## The six unlanded lanes

All are git worktrees under `~/projects/aify-repairs/`, staged in the index, **no commits**. Their
evidence is the `*-result.md` / `*-review.md` files beside them.

| lane | base | index tree | verdict | disposition |
|---|---|---|---|---|
| `herdr-instance-safety` | env `0036b70` | `b03b76dc` | **ACCEPT+SEALED** (independent R2) | LAND |
| `dashboard-integrated` | `a14100a8` | `ac4c124d` | R2 sealed at `93cd6f15`; **R5 REJECTS r4** | LAND AT `0bdb8d66` |
| `registry-credential-cli` | `a14100a8` | `b4e70715` | fixed + independently reviewed | ALREADY INSIDE `dashboard-integrated` |
| `ws-output-isolation` | `a14100a8` | `541a8eb7` | **BLOCKING REVISE, high** | DO NOT LAND |
| `dashboard-messenger` | `e16f54f6` | `eb493644` | superseded | DROP |
| `dashboard-mode-auth` | `a14100a8` | `d00a6324` | superseded | DROP |

**The DROPs are proven redundant, not assumed:** every path `dashboard-messenger` or
`dashboard-mode-auth` touches is also touched by `dashboard-integrated`, measured with `comm -23`
over the two name lists — zero files unique to either.

**And the table above was wrong once, in the direction that would have caused a second landing.** It
listed `registry-credential-cli` as its own LAND item. `dashboard-integrated` already contained that
fix: the lane is the integration of four lanes, not one, and the 38-file diffstat I first read was
truncated to its last 25 rows so the `mcp/stdio/` half was never on screen. Caught by applying the
lane and watching `git status` report nothing to do. **Read a diffstat's total, not its tail.**

**The dashboard-integrated tree chain, measured rather than taken from the reports:**
`93cd6f15` (R2 ACCEPT+SEALED) → `0bdb8d66` (r3, generated-env fixture, one python test file) →
`ac4c124d` (r4, `scripts/components.sh` ALONE). R5 reproduced a silent empty-root failure in r4's
resolver against a real-npm proxy layout on both platforms and recommended rejecting it. So the
landable tree is `0bdb8d66` — r4 is dropped, and the npm-discovery question goes back to being open.

## Still open, with the reason each is open

1. **herdr-aify** — the operator's headline ask. Design reconciled (`herdr-design-reconciled.md`),
   contract frozen (`herdr-opt-in-build-brief.md`), and only the FIRST slice (dedicated-instance
   safety) is built. The launcher, the shutdown lifecycle and ordinary-Herdr wrapper recovery are not.
2. **Console lag** — still **UNATTRIBUTED**, exactly as at v0.6.4. The WebSocket candidate was
   rejected: it evicts healthy clients on valid oversize output. `ws-output-isolation-admission-decision.md`
   says the smallest defensible replacement is a bounded shared FIFO outbox with producer credit —
   a protocol-level change needing the operator's approval, not a repair to slot in.
3. **Component probe writes `.npm`** — a "read-only" probe that is not read-only. r4's fix is rejected;
   the expanded CLI query (`NODE_DISABLE_COMPILE_CACHE=1` plus npm suppression flags) is evidence for
   a separate bounded decision, not a shipped contract.
4. **Bare shared-link authentication** — unfinished, untouched by any lane.
5. **Hermes exit 137** — root cause UNKNOWN. Strong evidence it is Hermes' own Node memory guard
   (`process.exit(137)` at heap 7.1GB), not an OS OOM kill. No candidate fix is justified without a
   retained-object capture.

## Standing facts that outlive this file

- The running backend and env are still OLD processes. Landing source changes nothing about what is
  running. Container rebuild, `install.sh` and any aify-env restart remain the operator's actions —
  they reap running workers.
- **Measured 2026-09-11 from `127.0.0.1:8802/health`, not carried forward:** aify-env is `0.6.3`,
  PID `112092`, `build 631df46d`, `codeOnDisk 50ad45fd`. The pair DISAGREES, so that daemon is not
  running the code on its disk — which is what `env-code-currency` exists to say, and here the cause
  is this session's own `a13c37e` landing after the daemon booted. **An earlier draft of this file
  said PID `77176` and hash `3b2bf8f9`**, copied from a report written earlier the previous day; the
  process had been replaced at 2026-09-10 19:25 and both figures were wrong. A PID is a mutable fact
  and belongs to the message that reads it.
- `aify-repairs/` is outside every repo, so its evidence is not reachable by anyone who clones. Any
  conclusion that has to survive gets copied into the repo that owns it.

## The review of `5b55ee3b..HEAD`

Eleven commits in aify-comms (81 files, +3185/-554), plus aify-env `0036b70`+`a13c37e` and
aify-wrapper `ce259bb`. Reviewed by reading the code, not the reports. **One real finding**, fixed in
`afa8c3cc`; everything else held up.

**THE FINDING — the sanitizer was gated by nothing.** `richMessageHtml` is the only place the
dashboard turns untrusted text into HTML; every other field in `chat-render.mjs` goes through `esc`.
Its defence is a DOMPurify policy, and DOMPurify needs a DOM — so under `node --test`,
`isSupported` is false and the function returns escaped text without ever reading the policy. The
tests therefore exercised only the escape fallback while reading as coverage of the formatter. The
real sanitization lives in `fixtures/messenger-browser.mjs`, and **nothing in the repo runs that
file**: its only reference anywhere is a comment in the test pointing at it. Widening the policy to
allow `data-*`, `target`, `javascript:` hrefs or `<script>` would have reddened no gate. The policy
is now a named export with mutation-proven tests. The contrast that confirms it is specific:
`https-origin.test.mjs` SPAWNS its fixture, so that one is genuinely exercised.

**What held up under reading:**

- `apiResponse` — both credentials attach AFTER the caller's headers, so `headers: {}` (needed to
  drop the JSON content-type for multipart) still authenticates. Verified in source, not assumed.
- Shared-file Download became an authenticated fetch to a blob, with `redirect: 'error'` so a
  redirect cannot carry a service credential to another origin. The pasted-image upload moved off a
  bare `fetch` that would have 401'd on any keyed service.
- The HTTPS override guard rejects an `http:` API origin on an `https:` page rather than upgrading
  it, and says why: the HTTP port need not speak TLS.
- `register-identity.js` got WEAKER and more honest. Its old warning asserted that the bridge's
  `AIFY_AGENT_ID` is what the shell hooks will use; the bridge's environment comes from the MCP
  client's configuration, which is not the parent shell's. The new text claims only what the bridge
  can see.

**One observation, not changed:** `messenger-reading.mjs`'s paging loop does not count an iteration
when `history.loading` is true, so a load that never settles leaves `jumping` true and suppresses
read receipts for that conversation indefinitely. Bounded in practice by the generation and
visibility checks at the top of each iteration, so it needs a hung request AND an unchanged
conversation. Left alone rather than edited into reviewed code.

## Still open after this session, and what each is waiting on

| item | state | waiting on |
|---|---|---|
| herdr-aify supervisor | **BUILT** 2026-09-12 | an operator-run end-to-end test; it starts a real aify-env, which is their action |
| herdr-aify Herdr bootstrap | **BUILT AND PROVEN** 2026-09-12 | nothing — see below |
| Ordinary Herdr keeps aify wrappers | **BUILT, PROVEN, INSTALLED** 2026-09-12 | nothing |
| Console lag | **UNATTRIBUTED** | an operator decision: the only defensible fix changes the event protocol |
| Component probe writes `.npm` | open | round 4's fix was rejected for a silent empty root |
| Bare shared-link navigation | partly addressed | a service browser-login feature — a security decision, not a bug fix |
| Hermes exit 137 | cause UNKNOWN | a retained-object capture nobody has |


## The Herdr integration, 2026-09-12

Two things the operator asked for, and the row above that said one of them needed "a generic Herdr
extension stock 0.9.0 lacks" was wrong. Stock 0.9.0 has both pieces; they were found by driving a
live Herdr rather than by reading more source.

**1. Ordinary Herdr keeps aify wrappers — PROVEN, and installed on this host.**

Herdr plans a resume only for agents reported under one of its own hardcoded (source, agent) pairs.
Reporting under `herdr:aify` makes it persist NO agent session for that pane, so the pane restores as
an empty shell carrying only `cwd` and `label`. A plugin `[[startup]]` hook — which Herdr runs once
after it restores a session — matches those labels against a ledger and puts the recorded wrapper
command back.

Measured, with the control in the same run: two official-source panes persisted a session and came
back with their agent; the aify-source pane came back empty with its label; the restore pass reported
ONE pane, not two. Then, with the plugin linked and nobody running any command, Herdr's own log:
`event startup, exit_code 0, status succeeded, restored 1 pane(s)`.

Installed here: wrappers re-rendered (`install.sh --client claude`, `--client hermes`, both verified
to carry the block, `~/.claude.json` byte-identical and still parsing), and the plugin linked from
`~/.aify-comms/mcp/stdio/node_modules/aify-wrapper/herdr-plugin` — the path `install.sh` refreshes,
so it survives reinstalls. `herdr plugin unlink aify.wrappers` undoes it.

**2. `herdr-aify` — BUILT, and its isolation half is proven; its daemon half is not.**

The isolated-Herdr half is what every measurement above ran on, so it is proven. What has never been
executed is the dedicated `aify-env` start, because that starts a real aify-env and supersession
there reaps the predecessor's workers — the operator's action, not mine.

**The Windows Job Object in the design notes was aspirational and is now corrected.** A real
kill-on-close Job needs a native addon and this package has none. Teardown works structurally
instead: the env runs in a PANE, its workers are its children, so stopping the server ends all of it;
the tree kill is a backstop that deliberately does not fire on a clean stop. The honest limit: a hard
kill of the launcher can leave processes, but it can never let the NEXT invocation adopt them — that
is enforced by a fresh UUID per invocation and a daemon that refuses a context whose receipts exist.

Design, measurements and the one install step: `~/projects/aify-wrapper/HERDR.md`.


## The review of the Herdr work, and what it found

I asked two adversarial reviews to attack the integration above, on separate slices. **Both found
real defects, and several contradicted claims I had made explicitly.** Fourteen are fixed, each with
the test that fails without it, and thirteen mutations were run to prove those tests can fail.

The four worth knowing, because each is a shape this project keeps meeting:

1. **A guard built on an ABSENCE.** The restore decided a pane was free when Herdr reported no agent
   on it. Herdr runs the startup hook again during a live handoff, and a handoff keeps the PTYs — so
   every RUNNING aify pane looked empty and would have had a command typed into it. The guard is now
   a positive identity, the pane's `terminal_id`, measured to change across a restart.
2. **A proof whose payload could not fail.** My end-to-end run replayed `echo
   RESTORED_BY_THE_PLUGIN` — one bare token. Driven afterwards with a real argument,
   `echo --flag "be terse"` printed `be` and `terse` on separate lines: `pane run` TYPES its
   arguments, and the boundary was gone. The feature did not work for real wrapper invocations, and
   the proof said it did.
3. **A safety claim the code contradicted.** The wrapper block says it can never fail a launch. Under
   `set -euo pipefail`, `VAR="$(cygpath ...)"` takes the substitution's exit status, so a cygpath
   failure aborted the wrapper before the agent ran — proven with a control that launched normally.
4. **A mitigation with no caller.** `HERDR.md` stated that wrappers clear the XDG roots for agents
   started inside a dedicated instance. The function that looked like the mitigation was never
   invoked from any production path and no wrapper touched XDG at all.

**And one defect only a live run could find.** After every unit test was green, running the restore
pass twice retyped into both panes: the record still named the pre-restart terminal, so the pane kept
looking free. Fixed, and re-proven live — the second pass now restores 0.

**A near-miss worth recording.** One mutation SURVIVED: the supervisor's test double set its failure
flag synchronously while a real child sets it a tick later, so a test written for exactly that defect
passed against the broken code. The double was corrected, not the test. A test that cannot fail is
worse than no test, and the only thing that exposed this one was mutating the code it guards.
