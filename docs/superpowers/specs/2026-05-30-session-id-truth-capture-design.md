# Session-ID Truth: in-session capture (claude + hermes)

**Date:** 2026-05-30
**Owner:** comms-tech-lead (Steven greenlit; claude first, then hermes)
**Fixes:** #138 (claude managed session-handle cross-contamination), #135 (hermes shared visible-session collision)

## Problem (root cause, verified)

Managed agents do not *capture* their own session id — they **guess** it from machine-global state:
- **claude** `adapters/claude.js discoverSessionId()` returns the freshest `.jsonl` across ALL `~/.claude/projects/*` by mtime. Whatever claude session is most active on the box wins, so every managed claude bridge stamps it. Observed: sc-claude/sc-manager/graph-tech-lead all got the operator's `comms-tech-lead` id `651b895f`; when sc-manager became the active resident in sand_castle, sc-claude adopted *its* session and "thought it was sc-manager."
- **hermes** discovery falls back to the gateway's single "most-recent" visible session, so multiple managed hermes agents converge on one id (sc-architect + sc-tester → `20260527_225730_b55c62` at bind-retry).

**Why tweaking discovery cannot work:** the operator runs a whole team in ONE directory. Two fresh same-folder claude sessions produce two transcripts in the same project dir with no filesystem signal mapping transcript→bridge. cwd-scoping and "freshest" are both fundamentally insufficient. The id must come from INSIDE each session.

## Key facts (verified 2026-05-30)

- claude project-dir encoding = `cwd.replace(/[^a-zA-Z0-9]/g,'-')` (e.g. `C:/Users/dev/sand_castle` → `C--Users-dev-sand-castle`).
- MCP children (server.js, claude-channel.js) are spawned by claude with **ParentProcessId = claude pid** (verified: pids 51208/21988 → parent 47876). Hooks claude spawns the same way share that ppid. The existing agent-binding (`aify-agent-<ppid>` in TMP) already relies on this.
- Claude hooks receive `{session_id, cwd, transcript_path}` on stdin — the one authoritative per-session signal.
- Heartbeat: `session-handle-heartbeat.js` prefers `discoverSessionId()` over `getCurrentSessionId()` (Plan 6 A1, to defeat stale hermes env). Keep that generic order; fix INSIDE the claude adapter so other runtimes are untouched.

## Design — claude

1. **`mcp/stdio/claude-session-store.js`** (new, ≤80 lines): `claudeSessionStorePath(pid,dir)` → `<dir>/aify-claude-session-<pid>.json`; `writeClaudeSessionId({sessionId,pid,dir})`; `readClaudeSessionId({pid,dir})`→id|null. dir defaults to TMP.
2. **`mcp/stdio/claude-session-hook.js`** (new, ≤60 lines): read stdin JSON, extract `session_id`, write via store keyed by `process.ppid` (=claude pid), TMP dir. Always exit 0 (never block claude). Tolerate empty/garbage stdin.
3. **`adapters/claude.js discoverSessionId(opts={})`**: precedence → (a) captured store id read by `process.ppid`; (b) `env.CLAUDE_SESSION_ID`; (c) cwd-scoped freshest in the agent's OWN project dir only (`env.AIFY_AGENT_CWD`); (d) `null`. NEVER machine-global. Inject `{env,homeDir,cwd,pid,dir}` for tests.
4. **`install.sh`** (claude branch): wire `SessionStart` + `UserPromptSubmit` hooks invoking `node <repo>/mcp/stdio/claude-session-hook.js` (forward-slash path via cygpath) through a settings file passed with `--settings`, alongside the existing `--mcp-config`. So ppid lines up (claude spawns node directly).

## Design — hermes

Analogous: the managed hermes worker must bind to the agent's OWN visible session, not gateway `session.most_recent`. Capture the visible-session id from the gateway handshake for THIS worker (the controller already opens a per-agent gateway WS) and store/report that; never fall back to global most_recent for a managed agent. (Detail during hermes task; ties into hermes-resident-controller.js + hermes.js adapter.)

## Tests (TDD)

- store round-trip (write→read, missing→null, bad json→null).
- hook: valid stdin writes correct id; empty/garbage → no throw, exit 0.
- claude adapter precedence: captured > env > cwd-scoped > null; **cross-repo isolation** (fresher transcript in a different project dir is NOT returned); same-cwd returns captured (proves the fix for team-in-one-dir).

## Rollout

Reinstall claude (+hermes) and **restart every `*-aify` agent** to load the hook. Live-verify: with sc-manager + sc-claude both resident in sand_castle, each reports its OWN id (no `651b895f` bleed, no shared hermes id). PID-mapping verified empirically before relying on it.
