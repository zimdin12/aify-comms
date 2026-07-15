# Review: 18 Local Commits (2026-07-13 through 2026-07-15)

**Range reviewed:** `a0c8ad9` through `8b40990` (18 commits, oldest to newest).

**Method:** inspected each commit patch and its final-HEAD call sites, checked touched tests/current coverage, and correlated claims with the current eight-issue brief. This is a code review, not a deployment certificate. Live claims in commit messages are historical evidence and are not relabeled PROVEN here unless independently re-exercised.

## Findings first

### R1 — HIGH: `comms_register` overclaims late registration; implementation is Claude-only

**Commits:** `b93c356`, with stale guidance from `209f6fc`.

`b93c356` says “registering now TURNS STATUS ON,” but the new machinery is explicitly `armClaudeTurnEndDetector` plus a Claude PID-keyed transcript capture. The register handler calls it for every runtime, but it no-ops unless `__runtimeAdapter.name === "claude-code"`. No symmetric Hermes late-binding/delivery/turn path was added. Current live evidence matches that gap: `comms_register` in Hermes returns `ClosedResourceError`, and the existing agent readback remains `stopped` with `wakeMode=disabled`.

The docs added by `209f6fc` now contradict the later code: `.claude/skills/aify-comms-debug/references/status.md:63-65` still states re-registering cannot help because identity is read once at boot, while `b93c356` intentionally makes it help for Claude.

**Required action:** implement honest Hermes late registration/linkage (Task 9 of the remediation plan), scope wording by runtime, and update both mirrored skill trees/current docs.

### R2 — MEDIUM: a user-visible compaction setting is documented in code as dead plumbing

**Commit:** `c1b7d50`.

The commit correctly records that `console_auto_confirm_claude_compaction` is not consumed by the bridge: the dashboard changes service settings, while the bridge reads a boot-time environment variable and never fetches the setting. Keeping a control that does nothing is misleading operator UX.

**Required action:** either wire the setting into bridge configuration/reconcile or remove/disable the dashboard control with an explicit explanation. Do not leave a live-looking no-op.

### R3 — MEDIUM: service-side OpenAI usage collection lacks direct tests despite handling credentials and uncertain semantics

**Commit:** `70472c3`.

The security/product decision is directionally correct: the card no longer publishes an unverified quota as truth, and auth mounts are read-only. However, the new `service/usage_openai.py` has no direct test in `service/tests`. It parses JWT payloads, searches mounted auth stores, performs a network request, caches results, and deliberately marks the value unverified — all behavior that should be pinned without real credentials or network.

**Required action:** add isolated tests for candidate selection, token extraction/redaction, timeout/error behavior, cache behavior, and the “unverified means never headline” response contract.

### R4 — MEDIUM: console repaint fix changed the real PTY without adding a regression test

**Commit:** `8b40990`.

The measured diagnosis is strong, but attach/refresh now intentionally resize/nudge the owned PTY and then pull a new snapshot. This is a side-effectful operator path and the commit touched only production files. Existing snapshot tests do not prove that resident consoles are never resized, managed consoles are nudged exactly as intended, or snapshot pull occurs after the repaint.

**Required action:** add a browser/unit seam around resize/refresh sequencing and ownership gating before further console work.

### R5 — LOW: the HistoryScreen “scrollback” implementation was based on a disproven premise and remains conceptual debt

**Commits:** `ad1afd9`, corrected by `8b40990`.

`ad1afd9` introduced `pyte.HistoryScreen` after assuming these TUIs scroll. `8b40990` later measured zero newline/scroll behavior and explicitly records that HistoryScreen captures nothing for this workload. The later commit correctly acknowledges the mistake and fixes repaint instead; this is not a current functional blocker, but dead complexity should not be described as real TUI history.

**Required action:** keep the honest limitation in docs and remove/de-scope HistoryScreen machinery only if it has no value for non-Ink terminals; do not do a drive-by removal without representative terminal samples.

### R6 — LOW: `aify-doctor` has no automated tests

**Commit:** `c6d2706`.

The operational tool solves a real class (installed source != running source), but no `*doctor*` test exists under `mcp/stdio/tests`. Platform/process inspection is exactly where Windows/WSL/Linux divergence causes silent false verdicts.

**Required action:** add fixture/injected-process tests for build stamp, installed bridge, running bridge age, agent identity, and strict exit status; keep real-host doctor as an end-to-end gate.

## Per-commit verdicts

1. **`a0c8ad9` — OK.** KEEP-CLEARED is the correct symmetric/idempotent counterpart to KEEP-FRESH. Null/unknown process truth is explicitly non-clearing and tests cover Claude and Hermes.
2. **`6396125` — OK.** CSS-only narrow-drawer fix; flex/min-width/wrapping changes are scoped to the reported overflow.
3. **`d418ffd` — OK, superseded.** Corrected an unconditional Windows path, then later commits replaced platform guessing with store search. No final-state objection.
4. **`224d50e` — OK with wording caveat.** Wrapper-side resume identity recovery is correct and anonymous plain sessions remain intentionally legal. The subject “never anonymous” is broader than implementation, but behavior is explicit.
5. **`0cba79a` — OK.** Resume commands now carry agent identity across runtime classes, and Claude hand-typed resume mirrors Hermes recovery. Codex hand-typed recovery remains honestly noted.
6. **`209f6fc` — CONCERN (R1).** Docs were internally consistent when written but became stale three commits later: they still claim re-register cannot repair late identity.
7. **`b93c356` — CONCERN (R1).** Good Claude late-identity fix and tests, but the product-level claim is not runtime-symmetric and does not solve Hermes manual registration.
8. **`c1b7d50` — CONCERN (R2).** Status/cache and real-PTY prompt parsing fixes are evidence-driven with captured bytes; however, the commit explicitly leaves a dashboard setting that does nothing.
9. **`46968b8` — OK.** Persistent live terminal screen is the correct mechanism for differential TUI output; bounded cache, fallback, alt-screen handling, split-escape test, and release path reduce risk. Single-worker assumption matches project architecture.
10. **`c669001` — OK, later truth-qualified.** Duration-based window classification and null preservation fix real parse errors. `70472c3` later correctly refuses to publish the endpoint as authoritative quota.
11. **`eb14f5f` — OK.** Searches actual token-bearing stores instead of OS/tool guesses, and preflight distinguishes missing/rejected/unreachable. Tests were added at the bridge layer.
12. **`c6d2706` — CONCERN (R6).** Valuable proof tool and docs, but platform-sensitive logic shipped without an automated test seam.
13. **`14ce0a3` — OK as a planning commit.** Clearly labels work not started and records evidence. Its HistoryScreen proposal was later disproven; the plan/history should be read with `8b40990`.
14. **`ad1afd9` — CONCERN (R5).** Rename/bookkeeping classification fix is strong and uses a real transcript. The scrollback half is superseded by later measured evidence.
15. **`dff8b21` — OK, coverage should be strengthened.** Reuses the send-path coldstart mechanism and guards resident/running cases. The commit itself added no focused test for the new agent-control `start` endpoint/UI path; include it in remediation regression coverage.
16. **`742f049` — OK and must be preserved.** Console is a PTY surface; it must not silently become the Hermes local web page or suppress Start. Any Hermes browser UI must be separate and explicit.
17. **`70472c3` — CONCERN (R3).** Correctly stops lying and moves collection out of per-agent bridges, but new sensitive service collector has no direct tests.
18. **`8b40990` — CONCERN (R4).** Correct measured repaint/root-cause fix, but PTY resize/nudge sequencing lacks regression coverage.

## Review disposition

- **No commit should be reverted wholesale.** Later commits intentionally supersede earlier assumptions, and the final-state direction is mostly sound.
- **Blocking before “all issues fixed”:** R1 and the eight-issue lifecycle/message defects.
- **Must fix in the same branch where touched:** R2-R4/R6 coverage/UX gaps if their production surfaces are modified by remediation.
- **Do not revive the legacy dashboard or Hermes-web-in-Console behavior.** Retire `service/dashboard.html`; new-dashboard only.

## Eight-issue execution disposition (2026-07-15)

1. **Console corruption — PASSES IN TESTS / design recorded.** `8b40990` is retained; the recommendation above keeps the real PTY and moves toward host-authored sequenced screen snapshots. A post-final-patch browser exercise is still required.
2. **Dashboard consolidation — PROVEN on the prior rebuild; final tree not redeployed.** Steven explicitly authorised retiring the legacy dashboard. `service/dashboard.html` is deleted and the historical routes redirect to Dashboard Next.
3. **No-op compaction setting — PASSES IN TESTS.** The misleading Dashboard Next toggle was removed; the API key remains response-compatible because the runtime only reads the setting at process launch.
4. **Routine chat reply contracts — PASSES IN TESTS.** `Expects reply` is opt-in and the outgoing `requireReply` value follows that explicit checkbox.
5. **Transient spawn-claim log flood — PASSES IN TESTS.** A first transient is debug-only when `AIFY_DEBUG=1`; warnings begin at three consecutive failures, repeat at most every 30 seconds, and recovery logs only after sustained failure.
6. **Claude channel subscription — ASSUMED/BLOCKED.** Static inspection confirms delivery uses the live MCP connection (`mcp.notification`) and the sidecar is parent-guarded. No fresh live reproduction of a sidecar restart with a still-open Claude session was performed, so self-heal is not certified.
7. **Managed Hermes teardown — PARTIAL PASSES IN TESTS, not deployment-certified.** Stop-worker now queues the owning bridge's terminal stop before updating control-plane state; the existing bridge stop path uses that control to reap the managed Hermes gateway/loop/daemon triad. The no-terminal-row survivor path and a fresh live process/port teardown on this exact patch remain unproven.
8. **Duplicate resident sessions — PASSES IN TESTS.** Reconciliation keeps a fresh owning bridge, retires only stale siblings, invalidates live-state, and does not cascade to an otherwise healthy agent.

**Independent verdict:** `comms-tech-lead` returned **REVISE**, not approve. Its assertion that dashboard retirement lacked scope was based on stale context and is superseded by Steven's explicit direction; its findings about missing exact deliverables and incomplete eight-issue evidence were valid and are addressed/qualified here. This tree must not be called unconditionally safe to deploy until the blocked live gates are closed.
