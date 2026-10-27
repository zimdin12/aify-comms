import fs from "fs";
import path from "path";

function bindingBaseDir(dir = "") {
  return dir || process.env.TEMP || process.env.TMP || "/tmp";
}

export function bindingFilePathForPid(pid, dir = "") {
  return path.join(bindingBaseDir(dir), `aify-agent-${pid || process.ppid || process.pid}`);
}

export function readAgentBindingFile({ pid, dir = "" } = {}) {
  const file = bindingFilePathForPid(pid, dir);
  try {
    const raw = fs.readFileSync(file, "utf-8").trim();
    if (!raw) return { agentId: "", bridgeId: "", pid: Number(pid || 0) || 0 };
    if (raw.startsWith("{")) {
      const parsed = JSON.parse(raw);
      return {
        agentId: String(parsed.agentId || "").trim(),
        bridgeId: String(parsed.bridgeId || "").trim(),
        pid: Number(parsed.pid || pid || 0) || 0,
      };
    }
    return {
      agentId: raw,
      bridgeId: "",
      pid: Number(pid || 0) || 0,
    };
  } catch {
    return { agentId: "", bridgeId: "", pid: Number(pid || 0) || 0 };
  }
}

/**
 * Bind `pid` to an agent. With `startedAt` (the writing process's start, ISO), a binding written by a process that
 * started LATER is kept: two comms processes under one parent (Claude reconnecting its MCP servers) can see their
 * registration answers arrive out of order, and the older one must not take the binding back (review of 9f6e1a90).
 *
 * BEST-EFFORT ORDERING, NOT COMPARE-AND-REPLACE. The read and the write are two steps: an older writer that read
 * before its successor wrote still overwrites it, and its exit then removes that binding. Only an older answer that
 * arrives after the successor's write is refused. A start that is equal to the held one, or unreadable on either
 * side, does not refuse. A start orders writers; it does not identify an owner. Returns whether it wrote.
 */
export function writeAgentBindingFile({ pid, agentId, bridgeId = "", startedAt = "", dir = "" } = {}) {
  const payload = {
    agentId: String(agentId || "").trim(),
    bridgeId: String(bridgeId || "").trim(),
    pid: Number(pid || process.pid) || process.pid,
    ...(startedAt ? { startedAt } : {}),
  };
  const file = bindingFilePathForPid(payload.pid, dir);
  if (startedAt) {
    let held = "";
    try { held = String(JSON.parse(fs.readFileSync(file, "utf-8")).startedAt || ""); } catch { /* none, or not ours to judge */ }
    if (held && Date.parse(held) > Date.parse(startedAt)) return false;
  }
  fs.writeFileSync(file, JSON.stringify(payload));
  return true;
}

export function removeAgentBindingFile({ pid, bridgeId = "", dir = "" } = {}) {
  const file = bindingFilePathForPid(pid, dir);
  try {
    const current = readAgentBindingFile({ pid, dir });
    const ownerBridgeId = String(current.bridgeId || "").trim();
    const requestedBridgeId = String(bridgeId || "").trim();
    if (ownerBridgeId && requestedBridgeId && ownerBridgeId !== requestedBridgeId) return false;
    fs.unlinkSync(file);
    return true;
  } catch {
    return false;
  }
}
