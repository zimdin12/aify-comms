# Hermes Durable Resume-Marker Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop managed/resident hermes agents from abandoning their session and starting **fresh after an aify-comms restart**, by ensuring the per-agent resume marker only ever holds the DURABLE `session_key` — never the ephemeral runtime sid.

**Root cause (confirmed, 2026-06-05):** The durable-key fix (`9a71b72`) corrected the *resolve* path (`resolveManagedHermesSession`, hermes-managed-host.js:2432-2468 uses `rowResumeKey` → durable key), but left the *delivery loop* untouched — its own comment admits it: *"Delivery … stays on the ephemeral sid; that split lives in the loop's waitForActiveSession, untouched here"* (line 2438). `waitForActiveSession` (hermes-managed-host.js:1086-1101) resolves the fallback session via `pickMostRecentSession` (which returns `rowRealId` — the **ephemeral** runtime id, hermes-gateway-protocol.js:280-283) and writes *that* into the resume marker via `writeMarker(id, recent, { tempDir })` (line 1097). So after every delivery the marker is re-poisoned with the ephemeral sid. On the next aify-comms restart the gateway `active_list` is empty and the SessionDB (`session.list`) is keyed by the durable `session_key`, so an ephemeral-sid marker matches no DB row → `resolveManagedHermesSession` clears it → the agent starts fresh and abandons its history.

**On-disk evidence:** `/tmp/aify-hermes-session-next-senior-dev` → `5af7c19c` (8-char ephemeral sid, written 2026-06-05 05:42, *after* the deployed fix), versus the correct `/tmp/aify-hermes-session-mp-senior-dev` → `20260603_004757_8d399a` (durable `YYYYMMDD_HHMMSS_hex` key). `next-tech-lead` (the reported symptom) has a port marker but no session marker at all.

**Architecture:** Single-line-class fix in the bridge: in the delivery loop's fallback, keep the ephemeral id for THIS delivery (`prompt.submit`/`steer` require it) but persist the DURABLE `rowResumeKey` to the marker. Add a restart-simulating regression test. The marker is the durable resume key everywhere; the ephemeral id never touches it.

**Tech Stack:** Node.js ESM (host bridge, `mcp/stdio/`), node:test.

**Deploy reality:** the running bridge is the native copy at `/home/dev/.aify-comms/mcp/stdio/` (env-bridge pid was 217153). A bridge edit requires re-running `install.sh` AND restarting the `*-aify` wrappers — a container rebuild is NOT involved (this is all host-side).

---

### Task 1: Reproduce the clobber in a unit test

**Files:**
- Test: `mcp/stdio/tests/hermes-durable-marker.test.js`
- Read for fixtures: `mcp/stdio/hermes-gateway-protocol.js` (`buildSessionActiveListFrame` response shape, `rowResumeKey`, `pickMostRecentSession`)

- [ ] **Step 1: Confirm the active_list row shape**

Read `mcp/stdio/hermes-gateway-protocol.js` lines 175-300 and note the row keys: a row carries an ephemeral runtime id (`rowRealId` → `id`/`runtime_id`/`sid`) AND a durable `session_key`. `rowResumeKey(row)` returns `session_key` (falling back to the realId only when absent). Build the test's fake `active_list` response with BOTH a distinct ephemeral `id` and a durable `session_key`.

- [ ] **Step 2: Write the failing test**

```js
#!/usr/bin/env node
// Repro of the "fresh session after aify-comms restart" bug: the delivery loop's
// fallback must persist the DURABLE session_key to the resume marker, never the
// ephemeral runtime id. Drives waitForActiveSession with injected ws + marker writer.
import assert from "node:assert/strict";
import { waitForActiveSession } from "../hermes-managed-host.js";

// An active_list row whose ephemeral runtime id differs from its durable key.
const EPHEMERAL = "5af7c19c";
const DURABLE = "20260605_054210_abc123";
const activeListResponse = {
  result: { sessions: [{ id: EPHEMERAL, session_key: DURABLE, last_active: "2026-06-05T05:42:10Z", attached: true }] },
};

const markerWrites = [];
const wsClient = {
  request: async () => activeListResponse, // every RPC returns the one live session
};

const res = await waitForActiveSession({
  id: "next-senior-dev",
  wanted: "",            // no bound id yet → falls into the most-recent fallback
  wsClient,
  nextId: (() => { let n = 1; return () => n++; })(),
  tempDir: "/tmp",
  writeMarker: (id, value) => markerWrites.push([id, value]),
  now: () => 10_000,
  freshnessFloor: 0,
  graceUntil: 0,         // grace already elapsed → fallback binds immediately
  log: () => {},
});

// Delivery uses the ephemeral id (correct — prompt.submit needs the live id).
assert.equal(res.sessionId ?? res, EPHEMERAL, "delivery binds the ephemeral live id");
// THE BUG: the marker must receive the DURABLE key, not the ephemeral id.
const lastMarker = markerWrites.filter((w) => w[0] === "next-senior-dev").pop();
assert.ok(lastMarker, "a marker was written for the fallback session");
assert.equal(lastMarker[1], DURABLE, "marker must hold the DURABLE session_key, not the ephemeral id");

console.log("hermes-durable-marker.test.js: all assertions passed");
```

> Before running, read `waitForActiveSession`'s real signature (hermes-managed-host.js:993-1010) and adjust the injected option names (`wsClient`/`nextId`/`writeMarker`/`now`/`freshnessFloor`/`graceUntil`/`log`) to match exactly, and read what the function RETURNS (a bare sessionId vs an object) to fix the `res.sessionId ?? res` assertion. Keep the two assertions (ephemeral for delivery, durable for marker) intact.

- [ ] **Step 3: Run the test to verify it fails**

Run: `node mcp/stdio/tests/hermes-durable-marker.test.js`
Expected: FAIL — the marker assertion shows `'5af7c19c' !== '20260605_054210_abc123'` (the loop wrote the ephemeral id).

---

### Task 2: Persist the durable key in the delivery loop

**Files:**
- Modify: `mcp/stdio/hermes-managed-host.js` (imports near line 72; `waitForActiveSession` fallback, lines 1086-1101)

- [ ] **Step 1: Ensure `pickMostRecentSessionRow` and `rowResumeKey` are imported**

In the `hermes-gateway-protocol.js` import block (around line 72 where `rowResumeKey` is already imported), confirm `pickMostRecentSessionRow` is present; if not, add it:

```js
  pickMostRecentSessionRow,
  rowResumeKey,
```

- [ ] **Step 2: Write the durable key to the marker**

Replace the fallback block (lines 1086-1101) so the marker gets the durable key while delivery keeps the ephemeral id:

```js
    if (!sessionId) {
      const recent = pickMostRecentSession(listResp); // ephemeral live id — for delivery
      if (recent) {
        const stamp = stampForSessionId(listResp, recent);
        const fresh = stamp >= freshnessFloor;
        const graceElapsed = now() >= graceUntil;
        if (fresh || graceElapsed) {
          sessionId = recent;
          if (id && recent !== wanted) {
            // DURABLE-MARKER FIX (2026-06-05, "fresh session after aify-comms restart"):
            // persist the DURABLE session_key to the resume marker, NEVER the ephemeral
            // runtime id. Writing the ephemeral id here re-poisoned the marker after every
            // delivery; on restart active_list is empty and the SessionDB is keyed by
            // session_key, so an ephemeral marker matched no row → resolve cleared it →
            // fresh. The ephemeral `recent` stays bound for THIS delivery only.
            const recentRow = pickMostRecentSessionRow(listResp);
            const durable = (recentRow && rowResumeKey(recentRow)) || recent;
            try {
              writeMarker(id, durable, { tempDir });
            } catch {
              /* best-effort marker write — never break delivery */
            }
            wanted = recent; // subsequent polls still track the live ephemeral id.
            log(
              fresh
                ? `[hermes-managed-host] '${id}': bound real session id ${recent} from gateway's most-recent live session (fallback); marker=${durable}.`
```

> Keep the rest of the original `log(...)` ternary and the closing braces exactly as they were after line 1104; only the lines shown above change (introduce `recentRow`/`durable`, write `durable` not `recent`).

- [ ] **Step 3: Run the repro test to verify it passes**

Run: `node mcp/stdio/tests/hermes-durable-marker.test.js`
Expected: PASS — `all assertions passed`.

- [ ] **Step 4: Syntax-check, run the existing hermes suite, commit**

```bash
node --check mcp/stdio/hermes-managed-host.js
node mcp/stdio/tests/hermes-managed-host.test.js
node mcp/stdio/tests/hermes-durable-marker.test.js
git add mcp/stdio/hermes-managed-host.js mcp/stdio/tests/hermes-durable-marker.test.js
git commit -m "fix(hermes): delivery loop persists DURABLE session_key to resume marker (stop fresh-on-restart)"
```

Expected: checks pass; existing `hermes-managed-host.test.js` still green.

---

### Task 3: Restart-simulation regression test (resolve survives an empty active_list)

**Files:**
- Test: `mcp/stdio/tests/hermes-resume-survives-restart.test.js`

- [ ] **Step 1: Write the failing/guard test**

Simulate the post-restart state: `session.active_list` is EMPTY (no live sessions after restart) but `session.list` (SessionDB) still holds the agent's durable row. A correct marker (durable key) must resolve to that row; an ephemeral marker must NOT silently clear when the durable session still exists.

```js
#!/usr/bin/env node
import assert from "node:assert/strict";
import { resolveManagedHermesSession } from "../hermes-managed-host.js";

const DURABLE = "20260605_054210_abc123";

// active_list empty (post-restart); session.list (DB) has the durable row.
const emptyActiveList = { result: { sessions: [] } };
const dbList = { result: { sessions: [{ id: "deadsid", session_key: DURABLE, last_active: "2026-06-05T05:42:10Z" }] } };

function clientReturning(frames) {
  let i = 0;
  return { request: async () => frames[i++], close() {} };
}

// (1) A DURABLE marker resolves from the DB even though active_list is empty.
{
  const writes = [];
  const res = await resolveManagedHermesSession({
    id: "next-senior-dev",
    marker: DURABLE,
    tempDir: "/tmp",
    openClient: async () => clientReturning([emptyActiveList, dbList]),
    writeMarker: (id, v) => writes.push(v),
    clearMarker: () => writes.push("__CLEARED__"),
    writeActiveSessionFile: () => {},
  });
  assert.equal(res.resolved, DURABLE, "durable marker resolves from SessionDB across restart");
  assert.ok(!writes.includes("__CLEARED__"), "a still-resumable marker must never be cleared");
}

console.log("hermes-resume-survives-restart.test.js: all assertions passed");
```

> Read `resolveManagedHermesSession`'s real signature (hermes-managed-host.js:2338-2360) and align the injected option names (`openClient`/`writeMarker`/`clearMarker`/`writeActiveSessionFile`/`activeSessionFile`) and its return shape (`{ resolved, source }`). The two assertions are the contract.

- [ ] **Step 2: Run it**

Run: `node mcp/stdio/tests/hermes-resume-survives-restart.test.js`
Expected: PASS (this path was already fixed by `3a38d30`; the test locks it so Task 2 can't regress it). If it FAILS, the DB-resolve path is broken too — fix `resolveManagedHermesSession` before proceeding.

- [ ] **Step 3: Commit**

```bash
git add mcp/stdio/tests/hermes-resume-survives-restart.test.js
git commit -m "test(hermes): lock durable-marker resolve across an empty post-restart active_list"
```

---

### Task 4: Clear the already-poisoned on-disk markers

**Files:** none (operational; the running agents' markers).

- [ ] **Step 1: Identify ephemeral-poisoned markers**

Durable keys contain underscores (`YYYYMMDD_HHMMSS_hex`); ephemeral sids are short bare hex. List the poisoned ones:

```bash
for f in /tmp/aify-hermes-session-*; do
  v=$(cat "$f" 2>/dev/null)
  case "$v" in
    *_*) : ;;                          # durable key — leave it
    "") : ;;
    *) echo "POISONED (ephemeral): $f -> $v" ;;
  esac
done
```

Expected: lists e.g. `/tmp/aify-hermes-session-next-senior-dev -> 5af7c19c`.

- [ ] **Step 2: Clear the poisoned markers so resolve re-resolves from the DB**

A poisoned (ephemeral) marker cannot resolve post-restart; removing it lets `resolveManagedHermesSession` fall to the most-recent live row (when the gateway is up) and, with Task 2 deployed, persist the durable key going forward. Only remove ephemeral-format markers — never the durable ones:

```bash
for f in /tmp/aify-hermes-session-*; do
  v=$(cat "$f" 2>/dev/null)
  case "$v" in
    *_*) : ;;            # durable — keep
    "") : ;;
    *) echo "removing poisoned marker $f ($v)"; rm -f "$f" ;;
  esac
done
```

> This is non-destructive to hermes history — the session still lives in the SessionDB; only the stale pointer file is removed. The agent re-binds its durable key on its next live attach.

---

### Task 5: Deploy and live-verify across a real restart

**Files:** none (deploy + verification).

- [ ] **Step 1: Re-copy the fixed bridge into the native dotfolder and restart wrappers**

```bash
bash install.sh --client hermes http://localhost:8800
```

Expected: installer reports the bridge copied to `~/.aify-comms`. Then restart each `hermes-aify` wrapper so it loads the fixed bridge.

- [ ] **Step 2: Confirm the deployed copy has the fix**

```bash
grep -n "DURABLE-MARKER FIX" /home/dev/.aify-comms/mcp/stdio/hermes-managed-host.js
```

Expected: the comment line prints (the native copy now carries Task 2).

- [ ] **Step 3: The restart acid-test**

With a hermes agent live and bound (e.g. `next-tech-lead`), verify its marker is durable, restart aify-comms, and confirm it resumes the SAME session (no fresh):

```bash
cat /tmp/aify-hermes-session-next-tech-lead   # expect a YYYYMMDD_HHMMSS_hex durable key
# (operator) restart aify-comms, then:
cat /tmp/aify-hermes-session-next-tech-lead   # expect the SAME durable key, unchanged
```

Expected: the marker stays a durable key before AND after the restart; the agent's Console resumes its prior session instead of "session not found"/fresh. Watch the gateway-host log for the resolve line:

```bash
grep -h "resolve-session" /home/dev/.local/state/aify-comms/hermes-gateway-host-*.log 2>/dev/null | tail
```

Expected: `agent '...' → <durable-key> (marker(db-resumable))` — NOT `cleared stale marker; will start fresh`.

> If no `hermes-gateway-host-*.log` exists, that gateway-host stderr logging isn't landing — file a follow-up; it's the diagnostic that would have caught this directly (the resolve path's `err(...)` lines are written but not captured to disk in this deploy).

---

### Task 6: Document the root cause and fix

**Files:**
- Modify: `KNOWN_ISSUES.md`, `DECISIONS.md`, `.claude/skills/aify-comms-debug/SKILL.md`, `.agents/skills/aify-comms-debug/SKILL.md`

- [ ] **Step 1: Add the troubleshooting entry (both skill copies, byte-identical)**

Add "managed/resident hermes starts a FRESH session after an aify-comms restart" to both `.claude/skills/aify-comms-debug/SKILL.md` and `.agents/skills/aify-comms-debug/SKILL.md`: symptom = restart → "session not found" → fresh; cause = the delivery loop persisted the ephemeral runtime sid into the resume marker, which is dead post-restart (the DB is keyed by the durable `session_key`); fix = the loop now persists the durable `rowResumeKey`; remediation for an already-poisoned marker = delete the ephemeral-format `/tmp/aify-hermes-session-<agent>` so it re-binds the durable key (Task 4).

- [ ] **Step 2: Record the decision in DECISIONS.md / KNOWN_ISSUES.md**

DECISIONS.md: "the per-agent hermes resume marker holds the DURABLE `session_key` ONLY; the ephemeral runtime sid is used solely for live delivery (`prompt.submit`/`steer`) and must never be written to the marker — the marker is the cross-restart resume target." KNOWN_ISSUES.md: note the gateway-host resolve-session log isn't reliably captured to disk in the current deploy (diagnostic gap surfaced by this bug).

- [ ] **Step 3: Commit**

```bash
git add KNOWN_ISSUES.md DECISIONS.md .claude/skills/aify-comms-debug/SKILL.md .agents/skills/aify-comms-debug/SKILL.md
git commit -m "docs(hermes): durable-only resume marker; fresh-on-restart root cause"
```

---

## Self-Review

**Spec coverage:** reproduce the clobber (Task 1) ✓; fix the loop to persist the durable key while delivery keeps the ephemeral id (Task 2) ✓; lock the post-restart DB-resolve path against regression (Task 3) ✓; clear already-poisoned markers (Task 4) ✓; deploy + real-restart acid-test (Task 5) ✓; docs (Task 6) ✓.

**Placeholder scan:** the two test tasks include explicit "read the real signature and align option names" notes — these are concrete adaptation instructions against named functions/line ranges, not "fill in later" holes; the fix code (Task 2 Step 2) and the marker-cleanup scripts (Task 4) are complete.

**Type consistency:** `pickMostRecentSession` (ephemeral id) vs `pickMostRecentSessionRow`+`rowResumeKey` (durable key) used consistently per the verified hermes-gateway-protocol.js definitions (280-283, 291, 175-177); `writeMarker(id, value, { tempDir })` signature matches the existing call at line 1097; `resolveManagedHermesSession` returns `{ resolved, source }` (Task 3) per hermes-managed-host.js:2499; the durable marker format `YYYYMMDD_HHMMSS_hex` (underscored) vs ephemeral bare-hex is the same discriminator used in Tasks 4's scripts and the on-disk evidence.
