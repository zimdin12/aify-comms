# Final polish + bridge bundle — one big finishing round (2026-07-02)

> Operator go-ahead: teams are DOWN → bridge-change window is OPEN. Goal: everything done,
> reviewed, tested, committed+pushed, publishable. Execute in order; each workstream ends
> green (tests + verification) before the next. If context compacts mid-run, resume from the
> first unchecked item. Working on `main` (repo's established workflow).

## Operator decisions baked in
- Reminders: NO backoff (risks stalling loops). LIGHT reminders (subject + message id only),
  every **Nth full** — N is a **setting** (`reply_reminder_full_every`, default 3, 0/1 = always full).
- queueIfBusy semantics = **"don't steer"** (deliver as next-turn work), not "don't interrupt".
- Do: bridge bundle now (teams down), runtimes.js refactor (#123), install.sh hermes
  detection (#174) keeping bash↔PS1 wrapper parity, anything else important found.
- Then: review hermes TUI integration upstream for borrowable improvements.
- Then: final docs/skills polish runs → commit+push → publish verdict.

## WS-A — Bridge bundle (mcp/stdio + install.sh; deploy = install.sh re-run + bridge restart)
- [x] A1 **Bug D root-cause + fix**: first queued send after env-bridge restart creates NO
  spawn attempt for cold hermes targets (~3min late, kicked by a 2nd send, then loses the
  180s backstop race); duplicate concurrent spawn_requests for one agent aren't coalesced.
  Find the claim/lazy-autostart-on-claim path (bridge poll loop; server comment api_v2.py:200
  "claim poll cadence + lazy-autostart-on-claim spawn"). Evidence: dispatch_events 170824-170859
  (2026-07-02 13:43-13:48), spawn_requests 13:46:30 + 13:47:11; claude target claimed in 5s,
  hermes targets never. Fix BOTH: (a) first-poll cold-spawn for queued runs to available managed
  hermes targets; (b) coalesce spawn requests per agent (second request while one is `running`
  for the same agent = attach, don't double-spawn). Unit-test in mcp/stdio/tests where feasible.
- [x] A2 **Compaction auto-confirm** (setting-gated): claude-console-prompts.js currently
  DELIBERATELY skips the `/compact → "Resume from summary"` prompt (comment: would summarize
  away the session) → managed agents stall there while managers wait (operator screenshot,
  lc-manager incident). Add setting `console_auto_confirm_claude_compaction` (default ON per
  operator pain; follow the existing `console_auto_confirm_claude_dev_channels` pattern:
  DEFAULT_SETTINGS + settings schema in new dashboard + bridge reads it) and auto-press Enter
  on that prompt when enabled. Keep the safety skip when the flag is off.
- [x] A3 **Real-cols reporting**: wrapper/bridge reports the PTY's ACTUAL cols/rows for
  resident mirrors → store in terminal_sessions.cols/rows (columns exist, db.py:392, currently
  0/None for residents) → GET /terminals uses stored cols as the render width (preferred over
  the infer_source_width heuristic; keep heuristic as fallback) → dashboards size the xterm
  exactly. Kills the live-redraw garble (inference≠actual width). Touch points:
  mcp/stdio terminal-runtime/wrapper (report), server ingest (heartbeat or terminal register),
  api_v2 GET /terminals render width priority: stored native cols > inferred > viewer.
- [x] A4 (contained-scope check) — RESOLVED: leave documented (task #136 stays pending; not a bounded change — needs a live repro of no_rollout to design the refresh path safely) **#136 codex stale session_handle** — investigate; fix only
  if it's a bounded change (e.g. clear/refresh handle on no_rollout + fall back to fresh
  thread); otherwise leave documented.
- [~] A-deploy: native copy refreshed + verified (diff clean); LIVE verification deferred to next team start — requires operator to restart the env bridge/wrappers (their terminal process); Bug D covered by 7-test regression suite meanwhile. `bash install.sh --client claude` + `--client hermes` + `--client codex` as
  applicable (refresh native copy), restart env bridge; verify: spawn a throwaway managed
  hermes+claude agent, send first message after bridge restart → worker spawns on FIRST send;
  /compact prompt auto-confirms; resident console renders at true width (no garble).
  REMOVE throwaway agents after.

## WS-B — Server engineering slice (#236) (service/ only; container rebuild)
- [x] B1 **Light reminders + `reply_reminder_full_every` setting** (default 3): reminder
  builder in api_v2 (search `reply_reminder`, "still needs an explicit reply"): non-Nth
  reminder body = one line "Reply owed to <message id>: <subject>" (no original body);
  every Nth = full current format. Count per contract (reminder_count exists —
  reply_reminder_max_count logic). Add to DEFAULT_SETTINGS + _SETTINGS_MIN(0) + new-dashboard
  settings schema (Contracts group). Tests: reminder body content per count.
- [x] B2 **queueIfBusy** ("don't steer"): trace send path for queueIfBusy=true to a busy
  steer-capable resident — server /messages/send handling (steer vs queue decision) + does
  the new-dashboard composer expose/send it (Options panel)? Operator saw immediate delivery.
  Also possible cause: status flap (target read `online` between turns). Fix whatever is
  actually broken; if composer lacks the toggle, add it (Queue button exists — verify it
  sends queueIfBusy=true).
- [x] B3 **Digest-wake for idle targets**: verify current behavior (steer-merge covers BUSY
  via 'merged' events). For an idle/cold managed target with N pending queued runs, the
  worker boot should deliver them as ONE combined turn (or sequential-merge on claim).
  Check claim path: does the claimer take one run at a time per turn? Implement coalescing
  at claim time (server: return/merge all queued runs for the agent in one delivery, mirroring
  the steer-merge 'merged' event format). Tests for merge behavior.

## WS-C — Refactors
- [x] C1 **runtimes.js split (#123)**: mechanical per-concern extraction (e.g. codex-config,
  hermes-launch, claude-session, shared utils) with re-exports from runtimes.js so importers
  don't change. `node --check` all files + run mcp/stdio/tests/*.test.js. NO behavior change.
- [x] C2 **install.sh hermes detection (#174)**: handle symlinked/pipx/shebang hermes installs.
  MUST keep the .ps1 hermes wrapper in parity with the bash one (memory: only hermes uses a
  ps1 wrapper). Test: `bash -n install.sh`.

## WS-D — Hermes TUI integration review (read-only upstream study)
- [x] Read the current (force-updated) hermes checkout: dashboard/web_server, api_server,
  session APIs, TUI attach mechanics. Look for: (1) a supported way to get session ids /
  attach without scraping; (2) real cols/size APIs (helps A3); (3) event streams that could
  replace polling; (4) anything simplifying hermes-managed-host.js (e.g. the readiness probe,
  token scrape). Output: short findings note in DECISIONS.md or KNOWN_ISSUES watch-items;
  borrow only cheap wins now, file the rest.

## WS-E — Final polish + verdict
- [x] Full test suites (python 803+, node dashboard 61+, mcp/stdio tests), containers rebuilt
  healthy, browser smoke (chat/sessions/environments/work/settings).
- [x] Docs/skills final coherence pass: new settings documented (settings schema + operations.md
  if needed), DECISIONS.md entries for A1-A3/B1-B3 decisions, KNOWN_ISSUES updates (Bug D →
  fixed; compaction stall → fixed; console garble → fixed), skill mirrors byte-identical.
- [x] Remove any throwaway test agents; git status clean; commit(s)+push.
- [x] Final report: what shipped, what was verified HOW, publish verdict.

## Safety rails
- Teams are down → bridge restarts are safe NOW; still: one change-set at a time, node --check
  + tests before every deploy, verify each bundle item live before moving on.
- Bridge code is delicate (hermes launch history) — after A-deploy, boot-verify hermes with
  `HERMES_DASHBOARD_TUI=1 hermes dashboard --port 9199 --host 127.0.0.1 --no-open --skip-build`.
- No opencode tests (GPU-tanking local LLM).
- Skill mirrors: any .claude/skills edit must be copied to .agents/skills (diff -rq must pass).
