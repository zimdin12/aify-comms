# Incoming-review fixes — holistic plan (2026-06-05)

**Goal:** Fix the 3 real issues + 3 minors found reviewing the peer's 12 incoming commits, WITHOUT regressing the durable-resume / status-v2 / console work that already composes cleanly. Each fix is independent, test-gated, and reversible.

**Hard constraints (don't break anything):**
- `install.sh` PS1 and bash wrapper blocks must stay in PARITY (this host runs the `.ps1`). PS1 edits require `install.sh --client hermes` rerun + wrapper relaunch to apply.
- mcp/stdio edits → rerun install.sh (native copy) + relaunch wrapper; service/ edits → container rebuild; commit before rebuild.
- Keep skill mirrors (.claude/.agents) byte-identical.
- Never run opencode tests.
- Don't touch the peer's composing-clean code paths except where listed.

---

## Issue 1 — 🔴 PS1 wrapper parity gap (Windows bug, affects THIS host)

**Problem:** the `--resume` DB-validate fix (`16de796`) only landed in the bash wrapper block (`install.sh:~1811`). The generated PowerShell wrapper (`install.sh:~2304-2316`) still discards the `resolve-session --explicit` result (`| Out-Null`) and passes the raw `$HermesSessionHandle` to `hermes --tui --resume`. On Windows the `.cmd` runs the `.ps1`, so a GC'd/dead explicit `--resume` still strands ("session not found").

**Files:** `install.sh` (PS1 block ~2304-2316), mirroring bash ~1811-1819.

**Fix:**
- Capture the resolve-session stdout in PS1: `$HermesExplicitResolved = & node "$BridgePath" resolve-session $HermesAifyAgentId --explicit $HermesSessionHandle 2>$null` (drop `| Out-Null`), trim it.
- Branch: if non-empty → `--tui --resume $HermesExplicitResolved`; if empty → bare `--tui` (fresh), mirroring bash `:1814-1819`.
- Fix the stale comment at `:2309`.

**Verify (don't break):**
- `bash install.sh --client hermes <url>` runs clean.
- Generated `~/.local/bin/hermes-aify.ps1` parses: `pwsh -NoProfile -NoLogo -Command "$null = [ScriptBlock]::Create((Get-Content -Raw ~/.local/bin/hermes-aify.ps1))"` exits 0.
- The corrected block is present (grep the generated PS1 for the resolved-branch).

---

## Issue 2 — 🟠 Fragile resume auto-answer (silent wrong-entry)

**Problem:** resume-picker answer is a blind positional `["\x1b[B","\r"]` (`claude-console-prompts.js:24`) assuming row1=compact, row2=full. A claude TUI menu reorder → silently selects the wrong resume entry.

**Files:** `mcp/stdio/claude-console-prompts.js`, tests `claude-console-prompts.test.js` + fixtures.

**Fix (cursor-aware, minimal):**
- In the resume rule, locate the line containing "Resume full session" (or the most-complete option) and the line carrying the cursor glyph (`❯`). Compute the row delta and emit that many `\x1b[B` then `\r` — or, if the cursor is already on the full-session line, just `\r`. If the full-session line can't be located, RETURN NULL (do not guess-press) — let the operator/30s settle handle it rather than risk a wrong pick.
- Keep the existing cursor-glyph + `not-working` + managed guards and the de-dup.

**Verify:** extend fixtures/tests for cursor-on-row1 (press down→enter) AND cursor-already-on-full (enter only) AND unlocatable-option (no keystrokes). Existing prose-injection negatives must stay green.

---

## Issue 3 — 🟡 Bridge self-exit margin (re-opened #173 risk)

**Problem:** `d999e32` cut the parent-death self-exit from ~90s to ~6s but still watches the IMMEDIATE ppid (`claude-channel.js:438`, `server.js:161`). If node's immediate parent is a transient `cmd`/`bash` (the #173 tree), a healthy bridge could self-kill within 6s.

**Files:** `mcp/stdio/claude-channel.js`, `mcp/stdio/server.js`.

**Fix (investigate → robust signal, non-breaking):**
- STEP 1 (investigate, needs a live managed claude): inspect the real managed-claude process tree — is node's immediate parent the durable `claude.exe`, or a transient shell? (Spawn one managed claude, walk `node→parent`.)
- IF immediate parent is durable `claude.exe` → 6s is safe; just add a comment documenting the verification. No code change.
- IF transient → adopt the #173 ancestor-walk: snapshot the nearest durable ancestor (`claude.exe`/`hermes`/the wrapper PID passed via env e.g. `AIFY_WRAPPER_PID`) instead of the immediate ppid, and watch THAT. Fall back to immediate ppid only if no durable ancestor resolves.
- Either way: keep the env bridge exclusion + the `unref` + the consecutive-miss reset.

**Verify:** `claude-channel-parent-guard.test.js` extended for the ancestor-resolution path; a live managed claude stays up >30s under load (no spurious self-exit).

---

## Minors (do if cheap, after the big 3)

- **M-A (test gap):** add a Python integration test that a console-working lease + live worker + `status_engine=new` serves `working`, and a dead-worker lease does NOT. (`service/tests/test_console_working_lease.py`.)
- **M-B (byproduct mirror, MY fix's gap):** apply Fix B's 30-min `in_turn` staleness clamp in `_compute_live_status_cache`'s byproduct read (`api_v2.py:~4456-4463`) so the served `new` path matches `_gather_status_inputs` exactly. Small, correctness-only.
- **M-C (regex, low):** tighten `INTERRUPT_RE` (`claude-console-spinner.js:20`) so self-referential prose ("esc to interrupt" written in chat) can't manufacture a `working` lease — require the spinner-glyph/footer context, not the bare phrase. 12s-bounded today, so low priority.

---

## Execution order
1. Issue 1 (PS1 parity) — isolated, highest value, install.sh only.
2. M-B (byproduct clamp) — service-only, completes my own fix.
3. Issue 2 (cursor-aware resume) — bridge-only.
4. Issue 3 (self-exit) — investigate first; code only if transient parent confirmed.
5. M-A, M-C — if time.

Each: implement → test → (service→rebuild / bridge→reinstall) → commit. Independent commits so any can be reverted alone.

**NOTE:** This plan is provisional — re-validate after pulling the peer's latest.

---

## RE-VALIDATION after pull (2026-06-05, peer commits `22536dd`/`98bcc91`/`8fe8baa`/`88a2317`)

- **4 bridge-test failures are PRE-EXISTING** (fail at the pre-peer baseline `532c772` too): `hermes-register-fresh-handle`, `hermes-runtime`, `server-url-fallback`, `wrapper-backed hermes child resident-claim`. Peer introduced NO regressions. (Backlog, environment/harness-bound — not in scope here.)
- **Issue 1 STILL OPEN and EXPANDED.** `98bcc91` added a bash `unset` for the dead-handle-on-fresh fix but again skipped PS1. So PS1 lagged on BOTH `16de796` (explicit-resume discard) AND `98bcc91` (clear-handle-on-fresh). The peer only ever edits the bash wrapper. → **DONE this round:** ported both into the PS1 block (`install.sh:~2314`); generated wrapper parses clean (pwsh ParseFile OK) + carries the block. This is my no-collision lane.
- **Issues #2 (cursor-resume) and #3 (self-exit) NOT fixed by the new commits, and they live in the peer's ACTIVELY-ITERATING files** (console/bridge/status — `22536dd` is more status work). → **FLAG to the peer rather than fix** (editing there now collides with their next push). 
- **M-B (byproduct staleness clamp)** is in `api_v2.py` status code the peer just touched (`22536dd`). → **DEFER** to avoid collision; low-impact edge.

**Net this round:** execute Issue 1 only (PS1 parity, peer's blind spot, this-host bug). Flag #2/#3/M-B to the peer. Re-pull before doing more.
