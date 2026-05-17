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

export function writeAgentBindingFile({ pid, agentId, bridgeId = "", dir = "" } = {}) {
  const payload = {
    agentId: String(agentId || "").trim(),
    bridgeId: String(bridgeId || "").trim(),
    pid: Number(pid || process.pid) || process.pid,
  };
  fs.writeFileSync(bindingFilePathForPid(payload.pid, dir), JSON.stringify(payload));
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
