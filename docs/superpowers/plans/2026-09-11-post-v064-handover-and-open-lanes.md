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
| herdr-aify Windows Job supervisor | not built | next slice; buildable now |
| herdr-aify Herdr bootstrap | not built | a generic Herdr extension stock 0.9.0 lacks |
| Console lag | **UNATTRIBUTED** | an operator decision: the only defensible fix changes the event protocol |
| Component probe writes `.npm` | open | round 4's fix was rejected for a silent empty root |
| Bare shared-link navigation | partly addressed | a service browser-login feature — a security decision, not a bug fix |
| Hermes exit 137 | cause UNKNOWN | a retained-object capture nobody has |
