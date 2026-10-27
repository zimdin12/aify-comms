// Registering the agent this bridge was LAUNCHED as, without anyone asking.
//
// `comms_register` is the tool an agent calls. This is the other path: at startup, if the wrapper exported
// an `AIFY_AGENT_ID`, the bridge registers that identity itself so the agent is reachable before it has
// executed a single turn. Nothing invokes it; it simply happens, which is why its failure mode is quiet.
//
// IT RETRIES, AND THE RETRY IS THE INTERESTING PART. The service may not be up yet when a wrapper starts —
// on a cold boot the container and the agent's shell race — so a failed registration re-arms itself up to
// eight times rather than leaving the agent unregistered until someone notices. The recursion is why this
// module exports a FACTORY: `_retriesLeft` is the function's own parameter, so the retry has to call the
// same function, and injecting the dispatch loop through the parameter list would have meant threading it
// through every recursive call. `makeAutoRegister({ ensureDispatchLoop })` closes over it once instead, and
// the body is unchanged.
//
// WHY THE LOOP IS INJECTED rather than imported is the same argument as `registration-tool.mjs`: importing
// `ensureDispatchLoop` would drag `runDispatchLoop` and 34 other functions into a module about startup
// registration. Auto-registration's relationship to the dispatch loop is "tell it to exist". It does not own
// the timer, the cadence or the lifecycle, and cannot start a second one.
//
// `computeInitialSessionHandle` IS EXPORTED SEPARATELY and is not inside the factory. It has no dependency
// on the loop, and it already had a test — which imported it from `server.js`, the bin entry point. Nothing
// should have to load the whole bridge to test nine lines of session-handle precedence, and that test now
// imports this module instead.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import {
  AIFY_AGENT_ID,
  AIFY_AGENT_ROLE,
  IS_MANAGED_DISPATCH,
  cleanEnvPlaceholder,
} from "./launch-identity.mjs";
import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { writeAgentBindingFile } from "./binding-file.js";
// `forgetRemoteAgent` is called on the 410 path below and was never imported — `node --check` parses
// an undefined name happily, and nothing exercises that branch, so it would have thrown
// ReferenceError the first time a tombstoned agent's bridge tried to auto-re-register. Same class as
// the `SERVICE_RUNTIME_PATHS` crash in doctor.js, found by the gate written after it.
import { REMOTE_AGENT_STATE, forgetRemoteAgent } from "./bridge-agent-state.mjs";
import { REDACTION_MARKER } from "./api-exposure.mjs";
import { BRIDGE_INSTANCE_ID, BRIDGE_STARTED_AT } from "./bridge-instance.mjs";
import { BRIDGE_BUILD_TAG } from "./bridge-build.mjs";
import { writeSessionIdMarker } from "./hermes-endpoint.js";
import {
  DEFAULT_CWD,
  normalizeRegistrationCwd,
  resolvedRuntimeConfigForRegistration,
} from "./registration-inputs.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";
import {
  defaultCapabilitiesForRuntime,
  launcherSessionHandle,
  detectRuntime,
  discoverCodexLiveBinding,
  hasCodexLiveAppServer,
} from "./runtimes.js";
import { defaultMachineId } from "./machine-id.mjs";
import { validateName } from "./safe-name.mjs";

// Bound from the one `defaultMachineId()` in `machine-id.mjs`, as several other bridge modules already do: it
// is a pure function of env and hostname, so deriving it here cannot disagree with deriving it elsewhere.
const MACHINE_ID = defaultMachineId();

export async function computeInitialSessionHandle({ adapter, envHandle, env = process.env }) {
  // A claude started from another claude's shell registers its own session, never the one it inherited.
  const ownSession = adapter?.sessionWhenStartedFromAClaudeShell?.(env) || "";
  if (ownSession) return ownSession;
  if (adapter && typeof adapter.discoverSessionId === "function") {
    try {
      const discovered = await adapter.discoverSessionId();
      if (discovered) return String(discovered).trim();
    } catch { /* swallow; fall through to env */ }
  }
  return String(envHandle || "").trim();
}

/**
 * Whether this bridge registers as its agent at all.
 *
 * A MANAGED agent's claude never carries CLAUDE_PID: every managed launch removes it (`NEVER_INHERITED` in
 * service/api_core/launch_env.py), and Claude Code does not set it for an MCP server. A managed bridge that
 * sees one belongs to a `claude` run from that worker's own shell, and the same-mode gate lets a managed
 * registration supersede by design (latest launch wins), so registering would take the worker over (review
 * of 7f638a65: a nested managed registration answered 200 and superseded the parent). A resident nested
 * bridge still registers: it names its own session and the service refuses it (409).
 */
export function registersAsTheAgent({ sessionMode, nestedSession }) {
  return !(sessionMode === "managed" && String(nestedSession || "").trim());
}

// The factory. `ensureDispatchLoop` is injected; see the header on why the retry makes this a
// factory rather than an extra parameter.
// `active`: whether a retry may still run. A process that has begun to leave answers false, so a refusal's
// retry cannot register a process that already reported itself gone. `onAttempt` receives each retry as it
// starts, so an owner that waits for registration before leaving waits for that one too (review of e1dfe132).
export function makeAutoRegister({ ensureDispatchLoop, active = () => true, onAttempt = () => {} }) {
  async function autoRegisterConfiguredAgent(_retriesLeft = 8) {
    // Audit 2026-06-28: the environment bridge must NEVER auto-register as an agent, because
    // inheriting a parent agent's AIFY_AGENT_ID would make it self-register as a resident and
    // clobber that agent's real registration. RETIRED IN v0.6.2 with the flag: there is no
    // environment bridge to exclude, and the guard could only ever fire on a resident that had
    // inherited the flag by accident -- silently refusing to register the agent the operator
    // launched. The three lines below carry the registration's real conditions.
    if (!IS_REMOTE || IS_MANAGED_DISPATCH || !AIFY_AGENT_ID) return;
    try { validateName(AIFY_AGENT_ID, "agent ID"); } catch (error) {
      console.error(`[aify] AIFY_AGENT_ID ignored: ${error.message}`);
      return;
    }
    const runtime = detectRuntime(process.env.AIFY_RUNTIME || "");
    // FIX 2 (2026-06-03): an UNEXPANDED literal like "${AIFY_AGENT_CWD}" (when the
    // wrapper didn't export it) is truthy and would bypass DEFAULT_CWD. Treat any
    // value still containing a ${...} placeholder as empty so DEFAULT_CWD applies.
    const rawAgentCwd = process.env.AIFY_AGENT_CWD || "";
    const agentCwd = /\$\{.*\}/.test(rawAgentCwd) ? "" : rawAgentCwd;
    const cwd = normalizeRegistrationCwd(runtime, agentCwd || DEFAULT_CWD);
    let runtimeConfig = resolvedRuntimeConfigForRegistration(runtime, null, cwd);
    const envHandle = launcherSessionHandle(runtime);
    // Plan 6 A2: discover authoritative, env fallback. See computeInitialSessionHandle above.
    // For codex this is the whole answer: the thread the launcher named, or none. Nothing here asks an
    // app server which thread is newest, in this workspace or any other (runtimes-codex.js says why).
    const sessionHandle = await computeInitialSessionHandle({ adapter: __runtimeAdapter, envHandle });
    if (runtime === "codex" && !hasCodexLiveAppServer(runtimeConfig)) {
      const codexLiveBinding = await discoverCodexLiveBinding({ sessionHandle });
      if (codexLiveBinding?.runtimeConfig) runtimeConfig = { ...runtimeConfig, ...codexLiveBinding.runtimeConfig };
    }
    // Native-session-id model (2026-06-03): bind agentId -> the REAL hermes
    // session id in the per-agent marker so the wrapper resumes the SAME session
    // next launch and the delivery loop targets it. Best-effort; never throws.
    if (runtime === "hermes" && AIFY_AGENT_ID && sessionHandle) {
      try { writeSessionIdMarker(AIFY_AGENT_ID, sessionHandle); } catch { /* best-effort */ }
    }
    // Wrapper-declared session mode + channel state. The *-aify wrappers set
    // AIFY_SESSION_MODE (resident default for human TTY, managed when
    // aify-comms spawns the wrapper) and AIFY_CHANNELS_ENABLED=1 when they
    // launched the runtime with the aify channel MCP loaded. We trust the
    // wrapper's declaration so the service's resident-cap strip (which
    // requires runtime_config.channelEnabled) gets the truth.
    const resolvedSessionMode = (() => {
      const explicit = String(process.env.AIFY_SESSION_MODE || "").trim().toLowerCase();
      return explicit === "managed" || explicit === "resident" ? explicit : "resident";
    })();
    const nestedSession = __runtimeAdapter?.sessionWhenStartedFromAClaudeShell?.() || "";
    if (!registersAsTheAgent({ sessionMode: resolvedSessionMode, nestedSession })) {
      console.error(`[aify] "${AIFY_AGENT_ID}" is managed and this claude was started from a Claude Code shell; not registering as it.`);
      return;
    }
    const channelsEnabled = String(process.env.AIFY_CHANNELS_ENABLED || "").trim() === "1";
    const capabilities = defaultCapabilitiesForRuntime(runtime, resolvedSessionMode, sessionHandle, runtimeConfig);
    const effectiveRuntimeConfig = channelsEnabled
      ? { ...(runtimeConfig || {}), channelEnabled: true }
      : (runtimeConfig || {});
    const payload = {
      agentId: AIFY_AGENT_ID,
      role: AIFY_AGENT_ROLE || "coder",
      name: AIFY_AGENT_ID,
      cwd,
      runtime,
      machineId: MACHINE_ID,
      bridgeId: BRIDGE_INSTANCE_ID,
      launchMode: "detached",
      sessionMode: resolvedSessionMode,
      sessionHandle,
      capabilities,
      runtimeConfig: effectiveRuntimeConfig,
      terminalId: cleanEnvPlaceholder(process.env.AIFY_TERMINAL_ID || ""),
      managedWrapperChild: String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1",
      restoreDeleted: true,
      autoRegister: true,
      // Tombstone-resurrection guard (2026-06-03): the service only clears an
      // agent tombstone for a GENUINE fresh relaunch — a bridge whose
      // bridgeStartedAt is newer than the tombstone's removed_at. Sending this
      // stops a still-running bridge from resurrecting a deliberately-removed
      // agent on its next passive auto re-register.
      bridgeStartedAt: BRIDGE_STARTED_AT,
      bridgeBuild: BRIDGE_BUILD_TAG,
      // Phase 4 race guard escape hatch (2026-05-31): when a same-mode resident
      // bridge is still LIVE, the service hard-rejects (409) a different bridge
      // re-registering this identity. Set AIFY_FORCE_REGISTER=1 to deliberately
      // take over after restarting the prior wrapper.
      force: String(process.env.AIFY_FORCE_REGISTER || "").trim() === "1",
    };
    try {
      const r = await httpCall("POST", "/agents", payload);
      // THE BINDING FIRST, with nothing awaited since the registration's answer, and never over a younger
      // process's (`startedAt`): a successor in the same parent (Claude reconnecting its MCP servers) registers and
      // writes its own, and this answer may arrive after it (review of 9f6e1a90).
      try {
        writeAgentBindingFile({ pid: process.ppid || process.pid, agentId: AIFY_AGENT_ID, bridgeId: BRIDGE_INSTANCE_ID, startedAt: BRIDGE_STARTED_AT });
      } catch {
        // Best effort; notification hooks can still operate after explicit comms_register.
      }
      // NO OWNER WRITE HERE. The registration route stores the owner itself (`registration_owner_bridge_id`,
      // service/api_core/ownership_authority.py): this bridge for a resident, the environment bridge's answer
      // kept for a managed agent. A PATCH that followed it here could arrive after a successor registered and
      // hand the agent back to this retired process, whose exit then took the agent offline (review of 9f6e1a90).
      let runtimeState = {};
      try {
        const agentInfo = await httpCall("GET", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}`);
        runtimeState = agentInfo.agent?.runtimeState || {};
      } catch {
        // Best effort.
      }
      REMOTE_AGENT_STATE.set(AIFY_AGENT_ID, { info: { ...payload, runtimeState } });
      ensureDispatchLoop();
      const transition = r.ownershipTransition ? ` (${r.ownershipTransition})` : "";
      console.error(`[aify] auto-registered "${AIFY_AGENT_ID}" as resident ${runtime}${sessionHandle ? ` session ${sessionHandle}` : ""}${transition}`);
    } catch (error) {
      const msg = String(error?.message || error || "");
      // Phase 4 race guard: a LIVE same-mode bridge already owns this identity.
      // RETRY, DON'T GIVE UP (2026-06-13, the sc-manager stale+deaf incident): a quick
      // close-and-relaunch always trips this guard — kill-prior killed the prior session
      // seconds ago, but its heartbeat lease makes it look live for up to ~150s. A
      // one-shot refusal left the session permanently unbound (mute sidecar = no inbound
      // delivery; stale status). The server now allows same-session-handle relaunch
      // takeover, and this retry loop covers older servers + genuine lease-expiry waits:
      // re-attempt every 30s for ~4 minutes. A genuinely-owned identity keeps refusing
      // (correct — operator hint stands); the dead prior simply ages out and a retry wins.
      if (/already has a LIVE/i.test(msg) || /force=true/i.test(msg)) {
        console.error(
          `[aify] auto-register for "${AIFY_AGENT_ID}" was refused — another live wrapper owns this session.\n` +
            `       ${msg}\n` +
            `       Retrying every 30s for ~4 minutes (a just-killed prior wrapper ages out of its lease).` +
            ` To take over immediately, relaunch with AIFY_FORCE_REGISTER=1.`,
        );
        const retriesLeft = Number.isFinite(_retriesLeft) ? _retriesLeft : 8;
        if (retriesLeft > 0) {
          const t = setTimeout(() => {
            if (active()) onAttempt(autoRegisterConfiguredAgent(retriesLeft - 1).catch(() => {}));
          }, 30_000);
          if (typeof t.unref === "function") t.unref();
        } else {
          console.error(`[aify] auto-register retries exhausted for "${AIFY_AGENT_ID}" — run comms_register in this session to bind it.`);
        }
      } else {
        console.error(`[aify] auto-register failed for "${AIFY_AGENT_ID}": ${msg}`);
      }
    }
  }
  return autoRegisterConfiguredAgent;
}

// RE-registering an agent from the state this bridge already holds. Same subject as the startup path above —
// registration the bridge performs on its OWN initiative rather than because a tool asked — and the reason
// it belongs beside it is that both build the same payload shape and both must sanitise the same unresolved
// `${AIFY_TERMINAL_ID}` placeholder. Two copies of that sanitising is how one of them gets fixed and the
// other does not.
/** `config` without the entries whose value is (or holds) the service's redaction marker. */
function withoutRedacted(config) {
  return Object.fromEntries(Object.entries(config || {}).filter(([, value]) => !JSON.stringify(value ?? "").includes(REDACTION_MARKER)));
}

export async function reregisterAgentFromState(agentId, state) {
  if (!state?.info) return false;
  const info = state.info;
  const payload = {
    agentId,
    role: info.role || "generic",
    name: info.name || agentId,
    cwd: info.cwd || "",
    model: info.model || "",
    description: info.description || "",
    instructions: info.instructions || "",
    runtime: info.runtime || "generic",
    machineId: info.machineId || MACHINE_ID,
    bridgeId: BRIDGE_INSTANCE_ID,
    launchMode: info.launchMode || "detached",
    sessionMode: info.sessionMode || "resident",
    sessionHandle: info.sessionHandle || "",
    managedBy: info.managedBy || "",
    capabilities: info.capabilities || [],
    // THE CACHED RECORD IS WHAT THE SERVICE SERVED, and it serves credentials redacted (688d8525). Posted back, the
    // marker became the stored gateway token. So what this machine resolves at startup (the hermes gateway from the
    // launcher's environment) is laid over the cache, and a value still carrying the marker is left out rather than
    // stored (review of 688d8525). Over, not instead: a codex binding found at startup is in the cache only.
    runtimeConfig: {
      ...withoutRedacted(info.runtimeConfig),
      ...withoutRedacted(resolvedRuntimeConfigForRegistration(info.runtime || "generic", info, info.cwd || DEFAULT_CWD)),
    },
    // R8: mirror the initial /agents register so a 404 auto-re-register does
    // not drop the console_terminal_attached binding. AIFY_TERMINAL_ID is
    // stable for the bridge process lifetime; fall back to cached info.
    terminalId: cleanEnvPlaceholder(process.env.AIFY_TERMINAL_ID || info.terminalId || ""),
    managedWrapperChild: String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1" || !!info.managedWrapperChild,
    autoRegister: true,
    // Tombstone-resurrection guard (2026-06-03): see autoRegisterConfiguredAgent.
    // A 404 auto-re-register from a lingering bridge must not resurrect a
    // deliberately-removed agent unless this bridge launched after the deletion.
    bridgeStartedAt: BRIDGE_STARTED_AT,
    bridgeBuild: BRIDGE_BUILD_TAG,
  };
  try {
    await httpCall("POST", "/agents", payload);
    console.error(`[aify] auto-re-registered "${agentId}" from cached state`);
    return true;
  } catch (error) {
    if (error?.status === 410) {
      forgetRemoteAgent(agentId, "server marked it intentionally removed");
      return false;
    }
    console.error(`[aify] auto-re-register failed for "${agentId}": ${error?.message || error}`);
    return false;
  }
}
