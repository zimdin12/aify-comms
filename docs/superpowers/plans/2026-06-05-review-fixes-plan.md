# Review Fixes Plan — from the big review round (2026-06-05)

Synthesis of the 8-agent review pass (see `2026-06-05-big-review-round.md`). comms-senior-dev's parallel pass merges in below once his reply lands (section "Merged from comms-senior-dev"). Every item tagged **CRITICAL** / **MEDIUM** / **OPTIONAL** with file:line + a SAFE, non-breaking fix. "Don't break it too much" — CRITICAL first, the rest behind explicit go-ahead.

## Headline
The system is in good shape. After the recent fixes, **all 5 runtimes feed `working` into BOTH status engines** (the `/heartbeat`→`_apply_status_event` bridge is generic), **all 31 `comms_*` tools are wired**, **flags match docs** (no stale/mismatched docs), and **session-resume + bash/PS1 parity** hold. The findings are a handful of real-but-bounded bugs and consistency/polish items — nothing systemic.

---

## CRITICAL (fix first — real bug, symmetry-that-bites, or misleads)

### C1 — Hermes kill-prior is a DEAD MATCHER (real leak, bounded)
The kill-prior reaper matches the **retired** synthetic key `--tui --resume aify-<agent>` (install.sh bash:1639-1641, PS1:2169-2172), but the actual launch now resumes the **real timestamp session id** (`HERMES_RESUME_REAL_ID`/`HERMES_EXPLICIT_RESOLVED`, bash:1820/1838/1845). So on relaunch the prior **visible TUI process can leak** — the reaper that was supposed to kill it matches nothing. Blast radius is bounded (the loop + gateway are still reaped by the cmdline-loop matcher + EXIT trap + NO-TUI backstop), so it's a process leak, not a delivery break. Same defect in BOTH bash and PS1 (symmetric).
**Fix:** match the real resumed id (or stamp a stable agent token / env marker on the TUI cmdline and match that). Bash + PS1. Update the kill-prior test. Risk: low (additive matcher, scoped to the agent).

### C2 — Dashboard raw-status leak: same agent, two labels (UI/UX A1)
`agentDisplayStatus()` (dashboard.html:3270-3276) emits `statusRaw` verbatim in the roster / Identity Directory / Control home (`:3345`, `:4089`), so transient internal states (`active`/`recovering`/`starting`/`cli-takeover`) surface as literal non-canonical words — while Chat normalizes via `chatPresenceForAgent()`/`statusBucketForPresence()` (`:5515`, `:5275`). Net: an agent reads "active" in one view, "online" in another. The 8-status vocab isn't honored everywhere.
**Fix:** one canonical `statusLabel(info)` + `statusKind(info)` used by every view (roster, detail, chat, console). Small, display-only. (Current dashboard is the live one; the fix also carries straight over to the React migration target — per operator, keep the new dashboard, just fix consistency here.) Risk: low.

### C3 — Dashboard: "Restart" missing from Identity Directory managed menu (UI/UX A2)
The managed action menu (dashboard.html:3743-3750) offers Stop / Reset(fresh context) / Remove but NOT **Restart** (the context-preserving re-spawn). So stopping a managed agent there leaves only Reset, which destroys context — and the Stop tooltip even points at the wrong reversal verb. Restart IS wired (`controlSession(id,'restart')`, used on the Sessions page).
**Fix:** add Restart to the managed menu + correct the Stop tooltip. Risk: low (verb already wired).

### C4 — Skill quality: oversized debug skill + non-spec frontmatter
(a) `aify-comms/SKILL.md:4` carries a non-spec `trigger:` frontmatter field — only `name`+`description` are spec; remove it (trivial). (b) `aify-comms-debug/SKILL.md` is **1,975 lines (~4× the 500-line guidance), zero progressive disclosure** — every trigger loads ~30k tokens. Split into one-level-deep `references/` files by the domains its own `## Contents` already names (status / hermes / codex / lifecycle / dispatch-bridge / pi / dashboard-console); SKILL.md becomes a router + preamble under 500 lines. (c) Move dated "FIXED (commit)" narration into collapsed history blocks so the body stops growing. Apply to BOTH `.claude/` and `.agents/` mirrors.
**Fix:** (a) now (trivial); (b)+(c) a focused skill-refactor (mechanical but sizeable — keep mirrors identical, install.sh copies the whole dir). Risk: (a) none; (b) low if done as pure content move + verified diff.

---

## MEDIUM (robustness / defense-in-depth — recommended, not urgent)

### M1 — New-engine `working` rides SOLELY on the heartbeat bridge
The `new` engine has no `active_run` input (`_gather_status_inputs` reads only `agent_status_state`). So managed codex/pi/opencode/hermes `working` exists ONLY because `reportTurnBusy`→`/heartbeat`→`_apply_status_event` (Fix A) feeds it. Works today, but if any future managed path asserts working via the dispatch-run lifecycle instead of `reportTurnBusy`, the new engine goes blind.
**Fix (defense-in-depth):** also call `_apply_status_event(turn_start/turn_end)` from the dispatch PATCH `status=running`/terminal transitions (api_v2.py ~18057), so working has two independent feeds. Risk: low (additive). Optional but cheap insurance.

### M2 — Runtime-list drift hazard: `dispatch-execution.js` ↔ `api_v2.py`
`NATIVE_MANAGED_RUNTIMES` (bridge) must stay in sync with `_NATIVE_MANAGED_RUNTIMES` (service); drift = a runtime silently no-ops (queued forever). **Fix:** a test asserting the two lists match (or a single shared source). Risk: none (test only).

### M3 — Codex #136 stale-handle resume (open backlog)
A managed codex whose stored handle's rollout was GC'd hard-fails the dispatch (by design: refuses auto-heal unless `resumePolicy=fresh_context`). The gap is the handle going stale. Existing backlog #136. **Fix:** out of scope for a quick pass; note + keep tracked. Decide later (auto-heal policy vs operator Recreate).

---

## OPTIONAL (polish / docs)
- **O1** server.js:5 header says "29 tools" — actual 31, omits the console tools. Trivial comment fix.
- **O2** comms_dispatch description doesn't carry the reply contract (inReplyTo / same-turn) that comms_send does (UI/UX B1). Add a one-line pointer.
- **O3** pi/opencode show NO resume command in the UI (correct — managed-only) but with no explanation; show a "managed-only, no resident resume" note (UI/UX A4).
- **O4** Usage SKILL.md dense reference-prose paragraphs (lines 45/77/115) → trim to pointers into `references/operations.md`.
- **O5** Mirror-parity guard: a `diff -r .claude/skills/aify-comms .agents/skills/aify-comms` pre-commit/CI check (or generate `.agents` from `.claude`) to enforce the existing "keep in sync" rule.
- **O6** Debug skill `## Contents` is ~40 of ~75 sections (drift); regenerate + add literal error tokens (`stale`, `queued`, `ECONNREFUSED`, `gateway websocket`) to its description for match rate. (Subsumed by C4 refactor.)
- **O7** Undocumented low-level tuning env vars (~20 `AIFY_HERMES_*`, poll cadences) — acceptable; optionally a "tuning knobs" reference table.
- **O8** Verb-set + label divergence between Identity Directory and Sessions control surfaces (UI/UX A3/A5) — settle in the React migration.
- **O9** (note, not a bug) claude console-lease feeds the new engine via read-time OR, not a write-time `_apply_status_event` like every other turn signal — asymmetry, currently compensated at read time.

---

## Confirmed GOOD (no action — recorded so we don't re-litigate)
All 5 runtimes feed BOTH engines; all 31 tools wired end-to-end; flags/settings match docs (incl. the binary-dependent HERMES_DASHBOARD_TUI note); resolve-session durable-key resume survives restart + bash/PS1 parity; teardown kills only the owned gateway child (load-bearing); console truthfulness (no "attached" for dead session); comms_send description + channel-wake payload + refused-send actionability are strong; pi Console synth-terminal mitigates #137.

---

## Merged from comms-senior-dev
*(pending his reply — run_1780671931086_8c66e318; reconcile agreements/disagreements here, then finalize the sequencing.)*

## Proposed sequencing (after merge + operator go-ahead)
1. C4(a) remove `trigger:` + O1 header (trivial, zero risk) — batch.
2. C1 hermes kill-prior matcher (bash+PS1+test) — the one real lifecycle bug.
3. C2 + C3 dashboard canonical status label + Restart verb (display-only).
4. M1 + M2 status defense-in-depth + runtime-list parity test.
5. C4(b)(c) debug-skill split — separate, careful, mirror-verified.
6. OPTIONAL batch behind explicit go-ahead.
