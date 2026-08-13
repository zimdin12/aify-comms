// What a registration RESOLVES before it registers.
//
// Registering an agent is not just a POST. Four things have to be worked out first, and each of them is a
// place where a wrong answer produces an agent that looks registered and does not work:
//
//   * the CWD, normalized — the runtime marker key is `sha256(cwd)`, so `C:\foo` and `C:/foo` are different
//     agents to Codex even though they are the same directory to the operator;
//   * the runtime MARKER — which live wrapper, of possibly several, this registration belongs to;
//   * the runtime CONFIG derived from that marker — gateway URL, app-server URL, channel flag;
//   * a captured Claude session id, claimed for this agent if one was recorded for our parent pid.
//
// None of it is specific to the `comms_register` tool. `makeResidentGatewayStatusReader`, `cleanupOnExit`
// and both boot sweeps resolve the same four things, because they are reconstructing or tearing down what a
// registration established. That shared readership is why this is an owner rather than four private helpers
// travelling with the tool.
//
// DEFAULT_CWD LIVES HERE, and that is a judgement worth stating rather than burying. It is
// `process.cwd()` captured once at load, and all seventeen of its readers want the same thing: the
// directory to record for an agent when the caller did not name one. It is the fallback inside
// `normalizeRegistrationCwd` and the default parameter of `resolvedRuntimeConfigForRegistration`, so an
// owner that did not hold it would have to import it back upward from `server.js` — the one direction this
// series does not allow. The alternative considered and rejected: capture `process.cwd()` a second time
// here. Two captures agree today only because nothing calls `chdir`, and a duplicated derivation is the
// exact defect these owner moves keep removing.

import {
  AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER,
  AIFY_HERMES_GATEWAY_URL,
} from "./hermes-gateway-config.mjs";
import {
  readCapturedClaudeSessionIdForPid,
  readClaudeSessionId,
  writeClaudeSessionId,
} from "./claude-session-store.js";
import { parseJson } from "./parse-json.mjs";
import { listRuntimeMarkers, readRuntimeMarker, selectClaudeChannelMarkerForParent } from "./runtime-markers.js";
import { normalizeRuntime } from "./runtimes.js";

// Captured at module load, before anything can chdir. See the header note on why it is owned here.
export const DEFAULT_CWD = process.cwd();

// Claim the session id the hook captured before we knew who we were, so the
// late-armed detector can resolve this session's transcript immediately (rather
// than waiting for the next hook fire — a channel-woken agent may never get one).
export function claimCapturedClaudeSession(agentId) {
  const id = String(agentId || "").trim();
  if (!id) return false;
  try {
    if (readClaudeSessionId({ agentId: id })) return false; // already keyed to us
    const sid = readCapturedClaudeSessionIdForPid({ pid: process.ppid || process.pid });
    if (!sid) return false;
    writeClaudeSessionId({ sessionId: sid, agentId: id });
    return true;
  } catch {
    return false;
  }
}

export function normalizeRegistrationCwd(runtime, cwd) {
  // Normalize Windows backslash cwds to forward slashes for Codex (and
  // Claude Code) at registration/marker-lookup time. Codex's path
  // deserializer on the Rust side rejects mixed/backslash paths, and the
  // runtime marker key is sha256(cwd) — so a caller that passes "C:\\foo"
  // must produce the same marker hash as a wrapper that wrote "C:/foo".
  // runtime-markers.js also normalizes internally, but we normalize here
  // too so the stored backend agent record matches what the bridge sends
  // to Codex at dispatch time.
  const normalizedRuntime = normalizeRuntime(runtime || "generic");
  const resolvedCwd = String(cwd || DEFAULT_CWD || process.cwd()).trim() || process.cwd();
  if (process.platform === "win32" && (normalizedRuntime === "codex" || normalizedRuntime === "claude-code")) {
    return resolvedCwd.replace(/\\/g, "/");
  }
  return resolvedCwd;
}

export function resolvedRuntimeMarker(runtime, cwd) {
  const normalizedRuntime = normalizeRuntime(runtime || "generic");
  const resolvedCwd = normalizeRegistrationCwd(normalizedRuntime, cwd);
  if (normalizedRuntime === "codex") {
    const liveMarkers = listRuntimeMarkers(normalizedRuntime, resolvedCwd);
    if (liveMarkers.length > 1) return null;
    return readRuntimeMarker(normalizedRuntime, resolvedCwd);
  }
  if (normalizedRuntime === "claude-code") {
    const ownParentPid = String(process.ppid || "");
    const seen = new Set();
    const candidates = [];
    for (const marker of [
      ...listRuntimeMarkers(normalizedRuntime, resolvedCwd),
      ...listRuntimeMarkers(normalizedRuntime, ""),
    ]) {
      const key = `${marker.cwd || ""}:${marker.pid || ""}:${marker.markerId || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      candidates.push(marker);
    }
    return selectClaudeChannelMarkerForParent(candidates, ownParentPid);
  }
  const exact = readRuntimeMarker(normalizedRuntime, resolvedCwd);
  if (exact) return exact;
  return null;
}

export function resolvedRuntimeConfigForRegistration(runtime, previousInfo = null, cwd = DEFAULT_CWD) {
  const normalizedRuntime = normalizeRuntime(runtime || "generic");
  const previousRuntimeConfig = parseJson(previousInfo?.runtimeConfig, {});
  const runtimeConfig = { ...previousRuntimeConfig };
  const marker = resolvedRuntimeMarker(normalizedRuntime, cwd);

  if (normalizedRuntime === "codex") {
    const appServerUrl = String(marker?.appServerUrl || process.env.AIFY_CODEX_APP_SERVER_URL || "").trim();
    const remoteAuthTokenEnv = String(process.env.AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV || "").trim();
    if (appServerUrl) runtimeConfig.appServerUrl = appServerUrl;
    else delete runtimeConfig.appServerUrl;
    if (remoteAuthTokenEnv) runtimeConfig.remoteAuthTokenEnv = remoteAuthTokenEnv;
    else delete runtimeConfig.remoteAuthTokenEnv;
  } else if (normalizedRuntime === "hermes") {
    const rawGatewayUrl = String(AIFY_HERMES_GATEWAY_URL || process.env.AIFY_HERMES_GATEWAY_URL || marker?.gatewayUrl || "").trim();
    // Reject unresolved hermes YAML interpolation placeholders. Operator-
    // reported 2026-05-25: hermes config.yaml env: AIFY_HERMES_GATEWAY_URL:
    // "${AIFY_HERMES_GATEWAY_URL}" — when hermes's own env doesn't have the
    // var set (because operator's hermes wasn't relaunched through the new
    // hermes-aify wrapper), interpolation falls back to the literal
    // placeholder string, which would pass through to runtime_config and
    // make the resident-channel controller fail later.
    const gatewayUrl = /^wss?:\/\//i.test(rawGatewayUrl) ? rawGatewayUrl : "";
    const gatewayTokenEnv = String(marker?.gatewayTokenEnv || AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER || process.env.AIFY_HERMES_GATEWAY_TOKEN_ENV || "").trim();
    if (gatewayUrl) runtimeConfig.gatewayUrl = gatewayUrl;
    else delete runtimeConfig.gatewayUrl;
    if (gatewayTokenEnv) runtimeConfig.gatewayTokenEnv = gatewayTokenEnv;
    else delete runtimeConfig.gatewayTokenEnv;
  } else if (normalizedRuntime === "claude-code") {
    if (marker?.channelEnabled) runtimeConfig.channelEnabled = true;
    else delete runtimeConfig.channelEnabled;
  }

  return runtimeConfig;
}
