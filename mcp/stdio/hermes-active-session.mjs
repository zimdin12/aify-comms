// Finding the hermes session an agent is bound to, and keeping it stable while work is delivered to it.
//
// Second extraction from `hermes-managed-host.js` in v0.5.4, after the gateway. Eight functions, ~260 lines,
// one subject: which of the sessions hermes reports is THE one for this agent, is it fresh enough to attach
// to, and how long to wait before concluding there is not one.
//
// WHY SESSION IDENTITY EARNS A MODULE. Picking the wrong row here is the failure mode behind this project's
// restart bugs: a managed restart creates a new session while the old one is still listed, and "most
// recently seen" briefly names the session that is about to die. `rowFreshnessStamp`, `rowRealIdLocal` and
// `stampForSessionId` exist so that decision is made in one place with one definition of fresh, instead of
// re-derived at each call site.
//
// ATTACH TIMING LIVES HERE, and that is an ownership decision rather than a mechanical one.
// `ATTACH_WAIT_MS` and `ATTACH_POLL_MS` have readers on both sides — `deliverRun` and `runPollCycle` still
// read them — but how long to wait for a session to become attachable IS a session concept, so this module
// owns them and the host imports them back. `TMP_DIR` had the identical two-sided shape and went the other
// way, to the neutral `hermes-env.mjs`, because a temp directory is environment identity and nothing to do
// with sessions. Two-sided readership means a constant needs a deliberate owner; it does not say which one.
//
// `resolveHermesPython` is here because `ensureStableSession` is its only caller: stabilising a session may
// require spawning hermes, and which python does that is part of doing so.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run and the wrappers relaunch.

import fs from "fs";
import path from "path";
import { spawnSync as nodeSpawnSync } from "node:child_process";

import { readSessionIdMarker, writeSessionIdMarker } from "./hermes-endpoint.js";
import { HERMES_CMD, TMP_DIR } from "./hermes-env.mjs";
import { sleep } from "./hermes-gateway.mjs";
import {
  buildSessionActiveListFrame,
  pickMostRecentSession,
  pickMostRecentSessionRow,
  pickSessionById,
  rowResumeKey,
} from "./hermes-gateway-protocol.js";
import { pinnedSessionId } from "./hermes-session-id.js";

export const ATTACH_WAIT_MS = Math.max(2000, Number(process.env.AIFY_HERMES_ATTACH_WAIT_MS || 25000));
export const ATTACH_POLL_MS = Math.max(100, Number(process.env.AIFY_HERMES_ATTACH_POLL_MS || 750));
const ATTACH_FRESH_GRACE_FRACTION = (() => {
  const raw = Number(process.env.AIFY_HERMES_ATTACH_FRESH_GRACE_FRACTION);
  if (Number.isFinite(raw) && raw >= 0 && raw <= 1) return raw;
  return 0.45;
})();


export function activeListRowsLocal(activeListResponse) {
  return Array.isArray(activeListResponse)
    ? activeListResponse
    : Array.isArray(activeListResponse?.result?.sessions)
    ? activeListResponse.result.sessions
    : Array.isArray(activeListResponse?.sessions)
    ? activeListResponse.sessions
    : Array.isArray(activeListResponse?.result)
    ? activeListResponse.result
    : [];
}


function rowFreshnessStamp(row) {
  return (
    Number(
      Date.parse(
        row?.last_active ||
          row?.lastActive ||
          row?.started_at ||
          row?.startedAt ||
          row?.created_at ||
          row?.createdAt ||
          0,
      ),
    ) || 0
  );
}


function rowRealIdLocal(row) {
  return String(row?.id || row?.session_id || row?.sessionId || "").trim();
}


function stampForSessionId(activeListResponse, recentId) {
  const wanted = String(recentId || "").trim();
  if (!wanted) return 0;
  for (const r of activeListRowsLocal(activeListResponse)) {
    if (rowRealIdLocal(r) === wanted) return rowFreshnessStamp(r);
  }
  return 0;
}


export function sessionKeyFor(agentId) {
  return pinnedSessionId(agentId);
}


export function resolveHermesPython(hermesCmd = HERMES_CMD) {
  const cmd = String(hermesCmd || "").trim();
  try {
    if (cmd && (cmd.includes("/") || cmd.includes("\\"))) {
      const dir = path.dirname(cmd);
      const candidates = [
        path.join(dir, "python.exe"),
        path.join(dir, "python3.exe"),
        path.join(dir, "python"),
        path.join(dir, "python3"),
      ];
      for (const c of candidates) {
        try {
          if (fs.existsSync(c)) return c;
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    /* ignore */
  }
  return process.platform === "win32" ? "python.exe" : "python3";
}


export function ensureStableSession({
  agentId,
  hermesCmd = HERMES_CMD,
  spawnSync,
} = {}) {
  const id = String(agentId || "").trim();
  if (!id) return false;
  const key = sessionKeyFor(id);
  const py = resolveHermesPython(hermesCmd);
  // One-shot python: create the row with the explicit id, title it, confirm.
  const code = [
    "import sys",
    "try:",
    "    from hermes_state import SessionDB",
    "    db = SessionDB()",
    "    db.create_session(sys.argv[1], source='aify-managed')",
    "    try:",
    "        db.set_session_title(sys.argv[1], sys.argv[1])",
    "    except Exception:",
    "        pass",
    "    ok = bool(db.get_session(sys.argv[1]))",
    "    try:",
    "        db.close()",
    "    except Exception:",
    "        pass",
    "    sys.exit(0 if ok else 1)",
    "except Exception as exc:",
    "    sys.stderr.write('ensure-session failed: %s\\n' % exc)",
    "    sys.exit(2)",
  ].join("\n");
  try {
    const runner = spawnSync || nodeSpawnSync;
    const res = runner(py, ["-c", code, key], {
      stdio: ["ignore", "ignore", "pipe"],
      encoding: "utf8",
      timeout: 30000,
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
    });
    if (res && res.status === 0) return true;
    if (res && res.stderr) {
      console.error(`[hermes-managed-host] ensureStableSession('${key}'): ${String(res.stderr).trim()}`);
    }
  } catch (error) {
    console.error(
      `[hermes-managed-host] ensureStableSession('${key}') failed (best-effort):`,
      error?.message || String(error),
    );
  }
  return false;
}


export async function waitForActiveSession({
  wsClient,
  agentId,
  // The agent's bound real session id. When omitted it's read from the marker.
  wantId,
  // Legacy: the old synthetic `aify-<agentId>` key. No longer the primary match
  // path; retained only so existing callers/tests that pass `key` don't break and
  // so a key-titled row can still resolve when no real id is known.
  key,
  nextId,
  tempDir = TMP_DIR,
  deadlineMs = ATTACH_WAIT_MS,
  intervalMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
  now = Date.now,
  // STALE-SESSION BIND-RACE GRACE (FIX A, 2026-06-03): on a RELAUNCH the per-agent
  // gateway host is REUSED (ensureGatewayHost → child=null), so the loop can poll
  // `session.active_list` BEFORE the freshly-relaunched `hermes --tui` re-attaches.
  // The pickMostRecentSession FALLBACK would then bind a STALE prior session (the
  // one being torn down) and persist it to the marker. The freshness floor (`since`,
  // the loop/poll start epoch captured at entry) protects against that — BUT as a
  // BOUNDED GRACE, not a permanent rejection. For the INITIAL grace window we prefer
  // a fresh row (stamp >= floor) and keep WAITING when only stale rows are present
  // (winning the relaunch race). ONCE the grace elapses we ACCEPT the most-recent
  // attached row even if its stamp predates delivery — presence in active_list means
  // the session is live/attached (a torn-down session leaves the list), so an idle
  // attached session must be delivered to, never requeued forever. The marker-matched
  // real id (PRIMARY) is ALWAYS accepted regardless of freshness/grace — it's the
  // intended session. `since` and `graceMs` are injectable for tests.
  since,
  // Bounded grace window (ms) during which a stale-stamped fallback row is still
  // skipped (relaunch race). Default: a fraction of the attach deadline. After this
  // elapses, a stale-but-attached fallback row is accepted. Injectable for tests.
  graceMs,
  // Marker read/write seams (best-effort; never throw in the delivery path).
  readMarker = readSessionIdMarker,
  writeMarker = writeSessionIdMarker,
  log = (msg) => console.error(msg),
} = {}) {
  // Freshness floor for the most-recent fallback. Captured ONCE at entry so it is
  // the moment this (relaunched) delivery attempt began — any session that
  // started before this is a stale pre-attach leftover.
  const freshnessFloor = Number.isFinite(Number(since)) ? Number(since) : now();
  // The grace window is bounded: prefer-fresh-and-wait until `graceUntil`, then
  // accept the most-recent attached row even if stale. Default to a fraction of the
  // deadline so the relaunch race has a window but delivery is never blocked forever.
  const resolvedGraceMs = Number.isFinite(Number(graceMs))
    ? Math.max(0, Number(graceMs))
    : Math.max(0, Math.round(deadlineMs * ATTACH_FRESH_GRACE_FRACTION));
  const graceUntil = freshnessFloor + resolvedGraceMs;
  const id = String(agentId || "").trim();
  // Resolve the wanted real id once: explicit arg wins, else the marker.
  let wanted = String(wantId || "").trim();
  if (!wanted && id) {
    try {
      wanted = String(readMarker(id, { tempDir }) || "").trim();
    } catch {
      wanted = "";
    }
  }
  const label = wanted || key || id || "(unbound)";

  const deadline = now() + deadlineMs;
  let attempts = 0;
  for (;;) {
    attempts += 1;
    let listResp = null;
    try {
      listResp = await wsClient.request(
        buildSessionActiveListFrame({ id: nextId(), currentSessionId: "" }),
      );
    } catch (err) {
      // active_list itself failed (e.g. gateway hiccup) — treat as not-ready and
      // keep polling within the deadline.
      listResp = null;
      if (attempts === 1) {
        log(`[hermes-managed-host] session.active_list error while awaiting attach: ${err?.message || String(err)}`);
      }
    }

    // (a) PRIMARY: the agent's bound real session id is live.
    let sessionId = wanted ? pickSessionById(listResp, wanted) : null;

    // (b) FALLBACK: no bound id, or the bound id isn't live yet → most-recent
    // live session for this gateway. Persist it so subsequent launches agree.
    // STALE-SESSION BIND-RACE GRACE (FIX A): a fresh row (stamp >= floor) is bound
    // immediately. A STALE row (stamp < floor) is only SKIPPED during the initial
    // grace window — that buys time for a freshly-relaunched `hermes --tui` to
    // re-attach so we don't bind the being-torn-down prior session. ONCE the grace
    // has elapsed, an attached row is the live session that's ready for work (a
    // torn-down session would have left active_list), so we ACCEPT it even though
    // its stamp predates delivery — otherwise an idle attached session requeues
    // forever. The PRIMARY marker-matched id above bypasses this entirely.
    if (!sessionId) {
      const recent = pickMostRecentSession(listResp);
      if (recent) {
        const stamp = stampForSessionId(listResp, recent);
        const fresh = stamp >= freshnessFloor;
        const graceElapsed = now() >= graceUntil;
        if (fresh || graceElapsed) {
          sessionId = recent;
          if (id && recent !== wanted) {
            // DURABLE-MARKER FIX (2026-06-05, "fresh session after aify-comms restart"):
            // persist the DURABLE session_key to the resume marker, NEVER the ephemeral
            // runtime id (`recent`). Writing the ephemeral id here re-poisoned the marker
            // after every delivery; on an aify-comms restart active_list is empty and the
            // SessionDB (session.list) is keyed by session_key, so an ephemeral-id marker
            // matched no row → resolveManagedHermesSession cleared it → the agent started
            // fresh and abandoned its history. The ephemeral `recent` stays bound for THIS
            // delivery only (prompt.submit/steer require the live id); the marker is durable.
            const recentRow = pickMostRecentSessionRow(listResp);
            const durable = (recentRow && rowResumeKey(recentRow)) || recent;
            // Capture the real id we fell back to (best-effort; never throws).
            try {
              writeMarker(id, durable, { tempDir });
            } catch {
              /* best-effort marker write — never break delivery */
            }
            wanted = recent; // subsequent polls now treat this as the bound id.
            log(
              fresh
                ? `[hermes-managed-host] '${id}': bound real session id ${recent} from gateway's most-recent live session (fallback).`
                : `[hermes-managed-host] '${id}': relaunch grace elapsed; binding most-recent ATTACHED session ${recent} despite stale stamp (idle-session delivery).`,
            );
          }
        } else if (attempts === 1) {
          log(
            `[hermes-managed-host] '${id}': most-recent gateway session ${recent} is stale (started before this delivery attempt); waiting up to ${resolvedGraceMs}ms (relaunch grace) for a fresh attach before binding it.`,
          );
        }
      }
    }

    if (sessionId) {
      if (attempts > 1) {
        log(`[hermes-managed-host] visible TUI session '${label}' attached after ${attempts} poll(s); delivering.`);
      }
      return sessionId;
    }
    if (now() >= deadline) return null;
    if (attempts === 1) {
      log(`[hermes-managed-host] visible TUI session '${label}' not attached yet; waiting up to ${deadlineMs}ms for resume…`);
    }
    await sleepImpl(intervalMs);
  }
}
