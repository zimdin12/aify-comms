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

## Merged from comms-senior-dev (received 2026-06-05, reconciled — zero disagreements)

His pass converged with mine + ADDED the root cause of C2 and one new critical:

- **His #1 = the ROOT of my C2 (now CRITICAL, merged).** Delivery PATCHes write a NON-VOCAB `agentStatus: "active"`: `claude-channel.js:322-327/335-340`, `hermes-channel.js:203-225`, `hermes-managed-host.js:768-790/964-968`; and `api_v2.py:18152-18155` writes `req.agentStatus` straight into `agents.status` with NO enum validation. That is the source; my UI pass found the symptom (dashboard renders the raw "active"). **Combined fix (all layers):** (a) stop emitting `agentStatus:"active"` from delivery-only PATCHes — `working` already derives from turnBusy/in_turn, so "active" is redundant/wrong; (b) server-side VALIDATE/normalize `agentStatus` against the 8-status enum in the dispatch PATCH; (c) the canonical `statusLabel()` in the dashboard (my C2); (d) fix the dashboard help line teaching "active" (his #3, dashboard.html:1706); (e) update the pinned test `test_api_v2_regressions.py:6101-6113` that expects `agentStatus:"active"`. **Trace before removing:** confirm nothing depends on `agents.status='active'` (live-state derivation overrides it, so it's vestigial — verify).
- **His #2 = NEW CRITICAL (MC2).** The managed-run `comms_register` guard error text (`server.js:3293-3297`) says "answer in final plain text; use comms_send only for separate updates" — which CONTRADICTS the managed reply contract (reply via `comms_send(inReplyTo=...)` same turn; final text is telemetry) documented at `server.js:3917-3922` + `runtimes.js:646-718`. An agent hitting the guard mid-request is told to do the wrong thing → stranded reply. **Fix:** reword the last sentence to direct a `comms_send(type=response, inReplyTo=<id>)` reply.
- His #4 (status_engine.py:34-43 comment drift re offline-vs-in_turn dominance) and #5 (opencode resume/handle UI language beyond the wired surface — = my O3/A4) → OPTIONAL, folded below.

## FINAL merged CRITICAL set (priority order)
- **MC1** — non-vocab `active` status: source PATCHes + server enum-validation + dashboard canonical label + help line + test (his #1 + #3 + my C2). [his P0]
- **MC2** — managed-register guard error text contradicts the reply contract (his #2). [his P0]
- **MC3** — hermes kill-prior dead matcher (my C1).
- **MC4** — dashboard missing Restart verb + wrong Stop tooltip (my C3).
- **MC5** — skill quality: remove `trigger:` field (trivial) + split the 1,975-line debug skill into `references/` (my C4).

(MEDIUM M1/M2/M3 and the OPTIONAL list unchanged above; his #4 + #5 join OPTIONAL.)

## Proposed sequencing (after operator go-ahead) — merged
1. **Trivial/zero-risk batch:** MC5(a) remove `trigger:` field + O1 "29 tools" header + his #4 comment cleanup.
2. **MC1 — non-vocab `active` (CRITICAL):** trace deps first, then stop emitting "active" in the 4 delivery PATCH sites + server enum-validate + dashboard canonical `statusLabel()` + help line + update the pinned test. Service rebuild + bridge reinstall. (Highest value — root + symptom in one.)
3. **MC2 — register-guard error text (CRITICAL):** reword `server.js:3293-3297`. Bridge reinstall.
4. **MC3 — hermes kill-prior matcher (CRITICAL):** match the real resumed id, bash+PS1+test. install.sh + relaunch.
5. **MC4 — dashboard Restart verb + Stop tooltip (CRITICAL):** add to Identity Directory managed menu. Rebuild.
6. **M1 + M2 (MEDIUM):** status defense-in-depth (dispatch-PATCH feed) + runtime-list parity test.
7. **MC5(b) — debug-skill split into `references/`:** separate, careful, mirror-verified (both .claude + .agents).
8. **OPTIONAL batch** behind explicit go-ahead.

Each step: implement → test → (service→rebuild / bridge→reinstall / install.sh→relaunch) → separate commit → push. comms-senior-dev to verify the MC1 active-removal doesn't break working-derivation.

---

## EXECUTION LOG (2026-06-06)

**CRITICAL — all shipped:** MC1 (active-status: bridges + server enum-validate + dashboard canonical label + help + test), MC2 (register-guard reply text), MC3 (hermes kill-prior real-id matcher, bash+PS1), MC4 (dashboard Restart verb), MC5(a) (trigger field). Plus the new **hermes stuck-`working`** fix (gateway "ready"/done → turn-end) and the laputa **stale leak-test** + nits.

**OPTIONAL — shipped:** his#4 (derive() comment), O2 (comms_dispatch reply contract), A4 (pi/opencode no-resume), M2 (runtime-list parity test), keepalive idle-grace gate, O5 (skill mirror-parity test), O7 (tuning-knobs reference in BRIDGE_SETUP.md).

**OPTIONAL — remaining:** MC5(b) debug-skill split into `references/`; O4 usage-skill prose trim.

**M1 — DEFERRED for safety (deliberate).** Feeding `_apply_status_event` from the dispatch lifecycle is FUTURE-PROOFING (no current managed path bypasses `reportTurnBusy`→`/heartbeat`, which already feeds both engines). The safe wiring would have to touch the nuanced terminal-state `turn_busy`-clearing logic (the send-deadlock fix at `api_v2.py:18107-18129`), which carries real regression risk for zero current benefit. Per "only act on what we're SURE of," deferred until a future managed path actually asserts working via the dispatch-run lifecycle. The 30-min `in_turn` backstop already bounds any worst case.

**React-migration carry-overs (do NOT churn the OLD dashboard — the new one is the target):**
- A3/A5 — settle ONE home for lifecycle verbs (Identity Directory vs Sessions divergence; "Stop wake"/"Resume wake" vs "Stop"/missing-"Resume" label divergence).
- B3 — the Hermes `mcp_aify_comms_*` tool-name prefix is only documented in the skill; surface it in the tool descriptions or normalize names in the new dashboard's agent view.
- A4 (UI copy) — show an explicit "managed-only · no resident resume" note for pi/opencode (the old dashboard just hides the block; the new one should explain).
