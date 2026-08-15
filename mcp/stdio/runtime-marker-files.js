// The runtime marker files on disk: list them, read them, and sweep the ones whose agent is gone.
//
// Extracted from `reap-managed-survivors.js` in v0.5.4, completing the split `proc-probes.js`
// started. That file now decides what to kill; these two supply what it decides FROM — one reads
// the process table, this one reads the markers processes leave behind. Closure measured before
// the move: `fs`, `os` and `path` and nothing the reaper declares.
//
// A MARKER IS A CLAIM THAT OUTLIVES ITS CLAIMANT. Managed runtimes write one when they start and
// are supposed to remove it when they stop — but the whole reason these reapers exist is that the
// triad is engineered to outlive its launcher, so a marker for a process nobody can find is the
// normal case after a bridge restart or a SIGKILL, not an anomaly.
//
// WHICH MAKES `sweepTombstonedMarkers` THE DANGEROUS ONE. It deletes marker files, and a marker
// deleted while its process is alive is a survivor nothing will ever reap — the process keeps its
// session and no sweep can name it again. That is why the tombstone test is against the SERVER's
// view of which agents are gone rather than against whether a pid answers.
//
// Bodies byte-identical to what stood in `reap-managed-survivors.js`.
import fs from "fs";
import os from "os";
import path from "path";

// Read the hermes triad markers from tempDir as
// [{ kind:'port'|'daemon-pid', agentId, value }]. Mirrors the file conventions
// in hermes-endpoint.js (aify-hermes-port-<agent>) and hermes-daemon.js
// (aify-hermes-daemon-pid-<agent>). Never throws → [] on failure.
export function defaultReadMarkers(tempDir = os.tmpdir(), { fs: fsImpl = fs } = {}) {
  const out = [];
  let entries = [];
  try {
    entries = fsImpl.readdirSync(tempDir);
  } catch {
    return out;
  }
  for (const name of entries) {
    let kind = null;
    let prefix = "";
    if (name.startsWith("aify-hermes-port-")) {
      kind = "port";
      prefix = "aify-hermes-port-";
    } else if (name.startsWith("aify-hermes-daemon-pid-")) {
      kind = "daemon-pid";
      prefix = "aify-hermes-daemon-pid-";
    } else {
      continue;
    }
    const agentId = name.slice(prefix.length);
    if (!agentId) continue;
    let value;
    try {
      value = parseInt(String(fsImpl.readFileSync(path.join(tempDir, name), "utf8")).trim(), 10);
    } catch {
      continue;
    }
    if (!Number.isInteger(value) || value <= 0) continue;
    out.push({ kind, agentId, value });
  }
  return out;
}


// Enumerate ALL per-agent hermes marker FILES in tempDir as
// [{ agentId, files:[absolutePath,...] }] grouped by agent. Covers the three
// kinds the triad writes: aify-hermes-port-<agent>, aify-hermes-daemon-pid-<agent>,
// and aify-hermes-key-<agent>. Used by the boot-time tombstoned-marker sweep
// (fix/hermes-leak P4) to delete dead markers for agents that no longer exist.
// Never throws → [] on failure.
export function defaultListMarkerFiles(tempDir = os.tmpdir(), { fs: fsImpl = fs } = {}) {
  const prefixes = [
    "aify-hermes-port-",
    "aify-hermes-daemon-pid-",
    "aify-hermes-key-",
  ];
  const byAgent = new Map();
  let entries = [];
  try {
    entries = fsImpl.readdirSync(tempDir);
  } catch {
    return [];
  }
  for (const name of entries) {
    const prefix = prefixes.find((p) => name.startsWith(p));
    if (!prefix) continue;
    const agentId = name.slice(prefix.length);
    if (!agentId) continue;
    if (!byAgent.has(agentId)) byAgent.set(agentId, []);
    byAgent.get(agentId).push(path.join(tempDir, name));
  }
  return Array.from(byAgent.entries()).map(([agentId, files]) => ({ agentId, files }));
}


// Pure: given the marker-file groups and the set of agent ids that still EXIST
// on the server (the live `/agents` keyset), return the marker agent ids that
// are tombstoned/unknown — i.e. have markers but are NOT a known agent. These
// are safe to delete machine-wide: a removed agent no longer exists in any env,
// so its leftover port/key/daemon markers can never belong to a live session.
// SAFETY: an agent still present in `knownAgentIds` is NEVER swept (it may be a
// co-located other-env's live agent). Fail-safe: callers pass null/undefined for
// knownAgentIds → return [] (unknown keyset must never become a blanket sweep).
export function tombstonedMarkerAgentIds(markerGroups = [], knownAgentIds) {
  if (knownAgentIds == null) return []; // unknown keyset → sweep nothing (fail-safe)
  const known = new Set(
    (Array.isArray(knownAgentIds) ? knownAgentIds : Array.from(knownAgentIds || []))
      .map((a) => String(a || "").trim())
      .filter(Boolean),
  );
  const out = [];
  for (const g of markerGroups || []) {
    const agentId = String(g?.agentId || "").trim();
    if (!agentId) continue;
    if (known.has(agentId)) continue; // still a live/known agent → keep its markers
    out.push(agentId);
  }
  return out;
}


// Boot-time tombstoned-marker sweep (fix/hermes-leak P4). Delete the marker
// FILES (aify-hermes-{port,daemon-pid,key}-<agent>) for every agent that has
// markers but no longer exists on the server. The companion to the survivor
// PROCESS sweep: that sweep kills orphaned processes; this clears the stale
// marker files a removed agent leaves behind so they don't accumulate and so the
// process sweep doesn't keep re-finding a phantom gateway/daemon. Pure +
// injectable. Fail-safe: knownAgentIds null/undefined → deletes NOTHING.
// Returns { swept:[{agentId,files}], errors }.
export function sweepTombstonedMarkers({
  knownAgentIds,
  tempDir = os.tmpdir(),
  listMarkerFiles = defaultListMarkerFiles,
  rm = (p) => fs.rmSync(p, { force: true }),
  log = (msg) => console.error(msg),
} = {}) {
  const swept = [];
  const errors = [];
  if (knownAgentIds == null) return { swept, errors, skipped: "known-agents-unavailable" };
  let groups = [];
  try {
    groups = listMarkerFiles(tempDir) || [];
  } catch {
    groups = [];
  }
  const tombstoned = new Set(tombstonedMarkerAgentIds(groups, knownAgentIds));
  for (const g of groups) {
    const agentId = String(g?.agentId || "").trim();
    if (!agentId || !tombstoned.has(agentId)) continue;
    const removed = [];
    for (const file of g.files || []) {
      try {
        rm(file);
        removed.push(file);
      } catch (err) {
        errors.push({ agentId, file, error: String(err?.message || err) });
      }
    }
    if (removed.length) {
      swept.push({ agentId, files: removed });
      try { log(`[aify] tombstoned-marker sweep: cleared ${removed.length} marker(s) for removed agent=${agentId}`); } catch { /* ignore */ }
    }
  }
  return { swept, errors };
}
