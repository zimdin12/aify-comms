# The work order, 2026-09-01

Everything outstanding, in the order it should be done. Operator's rule: **confirmed defects first,
exploration last.** A bughunt that finds nothing still costs a day; a confirmed defect that destroys a
process tree costs more than that.

Sources merged here: external review Round 7 (7 reviewers, `docs/aify-comms-issues-inbox.md`), the
v0.6.1 roadmap rows, the eight operator decisions
(`2026-09-01-operator-decisions.md`), and defects found while working.

**Severity is blast radius, not effort.** Anything that can end a process, forge an identity, or leak
a secret sorts above anything that is merely wrong.

---

## PHASE 1 — Confirmed, and can destroy or impersonate

### 1.1 ENV-H2 — the reaper verifies against the interpreter, not the launcher (aify-env, IN PROGRESS)
On Windows a shebang launcher is spawned as `bash.exe <launcher>`, `runner.mjs` recorded
`launcher: spec.command` (= `bash.exe`), and `defaultVerify` only substring-matches the live command
line. Every bash-launched process on the host matched, so a recycled pid meant `taskkill /T` on a
stranger's tree. POSIX spawns the launcher directly and was never exposed.
**DONE:** `protocol.mjs` now passes `body.launcher` through; `runner.mjs` records
`spec.launcher || spec.command`.
**LEFT:** the test (route arm: the runner receives the launcher; verify arm: `bash.exe` matches a
stranger's command line and the launcher path does not, with a positive control that our own process
still verifies), then aify-env + bridge suites, then commit.

### 1.2 SSE-M1 — console input's actor is forgeable (aify-comms, MINE)
`agents/console.py:278-283` requires `from` to name a REGISTERED agent but never checks the requester
IS it; SSE `comms_console_input` forwards the tool argument verbatim (`sse/console_tools.py:38,47`).
The route's own docstring says that value lands in `requested_by` and the `agent_console_input` audit
event, so **the audit trail is forgeable by construction**. The stdio side passes its own resolved
`caller` and is the model.
**NOT CLOSED BY THE SHARED SECRET.** Membership is not authority to act AS an agent. Say so.

### 1.3 aify-env adoption — starting it kills healthy workers (NEEDS THE OPERATOR'S RULING)
Supersession stops the predecessor, takes the port, then reaps the record whose owner it just killed.
Twice on 2026-09-01, five agents each, three mid-work. `bin/aify-env.mjs:386` states the current
intent, so this is a ruling to revisit rather than a bug to patch. Adoption satisfies "starting means
this one serves" without destroying work. Full chain in `2026-09-01-operator-decisions.md`.

### 1.4 managed-orphans — bridges that outlive their environment (CONFIRMED LIVE)
Measured 2026-09-01 with aify-env DOWN: `comms-senior-dev`, `graph-senior-dev` and `sc-architect`
(all `managed`/hermes) heartbeating every second with ZERO live terminals. The service's own
reconciler logged `orphan_workers_reaped: 4`, `orphan_workers_still_orphaned: 2`.
**The sharp question: they survived aify-env's shutdown**, whose guarantee is "processes managed by
aify-env die with it". Terminals died; these did not. Either they were never in the process record or
shutdown does not reach this class. Same family as 1.3, seen from the other side.

### 1.5 WRAP-M1 — keyEnv secrets baked into world-readable launchers (aify-wrapper)
Base64 into 0755 files (`registry.mjs:254,309-311`, `render.sh:57`, `install.sh:154,203`). Latent
only because no service sets `strictMcp: true` yet. Reinstall re-bakes, and blanks the key to `""`
when unset. Fix before anything starts setting that flag, not after.

---

## PHASE 2 — Confirmed, contained

- **SPLIT-M1** — no version-single-source gate in either new repo (3 uncoupled literals each).
  **Fold in the blind spot found here on 2026-09-01:** aify-comms' own gate checks the four files
  agree with EACH OTHER, never that they agree with the released tag. VERSION said `0.6.0` while HEAD
  was 391 commits past the v0.6.0 tag, and every gate was green.
- **WRAP-M2** — `pi-aify --check` does network I/O and can exit 1 before printing, breaking the
  `--check` contract the other three wrappers honour.
- **WRAP-M3** — `HARNESS_MCP_COMMAND` / `--mcp-transport sse` silently ignored in claude's default
  (non-strict) MCP mode.
- **ENV-L1** — a corrupt process record is laundered to "no orphans" and then overwritten: permanent
  leak. Fails OPEN, which is the wrong direction for this file.
- **CRED-L1** — heartbeat `bridge*` liveness/build forgeable via a self-asserted `bridgeId` (inside
  the shared-key boundary, so it downgrades once 1.2's lesson is applied).
- **CRED-L2** — `credentialRef` grammar divergence between producer and consumer.
- **DOCTOR-L1** — `ok:false` context-window partial shares the benign `~` glyph, plus a stale comment.
- **SPLIT-L1** — two 0-byte `how` files; the `mcp/stdio/how` one ships to every install AND the
  container.
- **SPLIT-L2** — the registry contract producer cannot self-detect consumer drift.
- **SPLIT-L3** — the new repos' `.gitignore` omit `.env`.
- **DISP-L1** — the 4h lease ceiling is bypassed for legacy rows with no `turn_started_at`.
- **Watch-items from the review, pre-existing, not delta:** `_settle_inbox_read` can close an
  in-flight `require_reply` run as answered with a synthesized body
  (`inbox_read_receipts.py:44-54`); `DELETE /agents/{id}` is ungated.

---

## PHASE 3 — Release hygiene, once Phases 1-2 land

1. Bump `VERSION` to `0.6.1` plus `mcp/stdio/version.js`, `package.json`, `package-lock.json`,
   `.claude-plugin/plugin.json`.
2. Run all five suites.
3. `bash scripts/stamp.sh`, rebuild, re-run `install.sh` per client (mcp/stdio changed).
4. `aify-comms doctor`.
5. **Operator tags v0.6.1.** Round 7 reviewed `dc01812a→e8856126`, which covers the 38 commits that
   are mine, so the "needs an independent reviewer" blocker is discharged.

---

## PHASE 4 — Operator-requested, confirmed scope

- **API_KEY=banana + a dashboard login PROMPT.** Approved. Costs a 401 for every client until each is
  reinstalled AND its wrapper restarted — measured 2026-09-01 as 4 real resident sessions
  (comms-tech-lead, mc-manager, sc-manager, graph-tech-lead), the other live bridges being 1.4's
  orphans. Build the prompt FIRST, then key + rebuild + reinstall in one window.
  **No loopback shortcut:** nothing in the service reads client IP, and with the port published on
  `0.0.0.0` behind a docker bridge, host and remote traffic almost certainly arrive identically.
  Unproven, and being wrong would exempt remote callers from auth, so it fails closed.
- **Row 3 batch #6/#7/#8/#9** — written, UNCOMMITTED, unverified. Finish and run five suites.
- **Row 3 #2** — actor on four lifecycle verbs. **Row 3 #3** — description-parity gate.
- **`comms_send` unslop** — tighten all 2,636 B rather than cut the 855 B reply contract.
  `tools/list` costs ~7.9k tokens per agent per turn, so this is paid every turn by every agent.
- **Freeze ledger** — merge the THREE launchability sites onto `LAUNCHABLE_RUNTIMES`, name the TWO
  turn-tracking sites separately, lower the frozen count honestly.
- **Spawn stranding** — bound `_has_claimable_spawn_request` by freshness (fixes every cause), add
  contract validation at creation, one-off cleanup of stuck rows. NO standing reaper.
- **Terminal write path** — measure lowering the 64KB tail cap first (8x, no re-architecture); then
  move the two status-path readers off the stored tail; then write it lazily. ~870x amplification,
  ~2.6 MB/s of SQLite writes per busy agent for ~15 KB/s of real output, on a single writer behind
  one lock.
- **Row 4 F6** — `keyEnv` binds nothing (`registry.mjs:192` is reached only by `strictMcpEntriesFor`,
  which filters on `strictMcp === true`, absent from the live registry entry). Pairs with 1.5.
- **Row 4 F7** — the ws call-site test catches bare `(WebSocketDisconnect, Exception)` and never
  asserts close 1008.

---

## PHASE 5 — Improvements the operator asked for

- **aify-env TUI, Row 6 item 3 (colour).** Items 1 and 2 are done; differential rendering measured at
  0 bytes for an unchanged frame and 133 for a one-line change, against 4,687 for the old repaint.
  Item 3 needs a state-versus-appearance census before any design.
- **Dashboard terminal input, Row 1.** BLOCKED on 1.2/1.3's authority question: `/ws`'s Origin check
  cannot authorise input, because omitting `Origin` is the documented way bridges connect (proven
  live: no Origin -> 101, `evil.example` -> 403). Wiring keystrokes today lets anything reaching
  :8800 type into an agent's terminal.
- **Dashboard UX beyond Row 2.** Row 2 shipped (three dead CSS rules, triage tile keyboard), both
  gated. Next pass wants a real list of complaints rather than a sweep.

---

## PHASE 6 — Exploration, LAST

Only once the confirmed list above is empty.

- Scoped reviews of the areas the split moved (host tier, credential carrier, registry contract).
- Bughunt rounds.
- The duplicate-session-handle mechanism: still unknown, two traced explanations disproved against
  hermes' own source. Report-only today.
- The removal defect: every deletion leaks a process.

---

## Standing constraints that outrank any item above

- The fleet is LIVE. Never start, restart, supersede or kill shared infrastructure without the
  operator, AND without the quiescence probe coming back clean.
- All five suites green before every commit, each read from its own runner's exit status.
- No AI-attribution trailers.
- Operator-only: publishing a release or tag, posting anywhere public, deleting data, spending money.
