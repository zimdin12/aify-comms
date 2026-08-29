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

import { mayClaimEnvironmentOwnership } from "./environment-ownership-claim.mjs";
import {
  AIFY_AGENT_ID,
  AIFY_AGENT_ROLE,
  IS_ENVIRONMENT_BRIDGE,
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
import { BRIDGE_INSTANCE_ID, BRIDGE_STARTED_AT } from "./bridge-instance.mjs";
import { writeSessionIdMarker } from "./hermes-endpoint.js";
import {
  DEFAULT_CWD,
  normalizeRegistrationCwd,
  resolvedRuntimeConfigForRegistration,
} from "./registration-inputs.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";
import {
  defaultCapabilitiesForRuntime,
  defaultMachineId,
  defaultSessionHandleForRuntime,
  detectRuntime,
  discoverCodexLiveBinding,
  discoverCodexLiveThreadId,
  hasCodexLiveAppServer,
} from "./runtimes.js";
import { validateName } from "./safe-name.mjs";

// Bound from the one `defaultMachineId()` in `runtimes.js`, as several other bridge modules already do: it
// is a pure function of env and hostname, so deriving it here cannot disagree with deriving it elsewhere.
const MACHINE_ID = defaultMachineId();

export async function computeInitialSessionHandle({ adapter, envHandle }) {
  if (adapter && typeof adapter.discoverSessionId === "function") {
    try {
      const discovered = await adapter.discoverSessionId();
      if (discovered) return String(discovered).trim();
    } catch { /* swallow; fall through to env */ }
  }
  return String(envHandle || "").trim();
}

// The factory. `ensureDispatchLoop` is injected; see the header on why the retry makes this a
// factory rather than an extra parameter.
export function makeAutoRegister({ ensureDispatchLoop }) {
  async function autoRegisterConfiguredAgent(_retriesLeft = 8) {
    // Audit 2026-06-28: the environment bridge must NEVER auto-register as an agent. It's always
    // remote and not managed-dispatch, so if it inherits a parent agent's AIFY_AGENT_ID (the known
    // gotcha) it would self-register as a resident agent and clobber that agent's real registration.
    // The launcher scrubs the env, but guard in-code too (belt-and-suspenders, like the line-5832
    // harness-death guard which already excludes the env-bridge).
    if (IS_ENVIRONMENT_BRIDGE) return;
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
    // Same ${...}-placeholder guard as AIFY_AGENT_CWD above (FIX 2): an unexpanded
    // `AIFY_SESSION_HANDLE="${HERMES_SESSION_ID}"` (wrapper/config var unset) must NOT
    // become the registered handle — it poisons the agent→session binding. Strip it so
    // the runtime default / discover path applies instead (2026-06-04).
    const rawSessionHandle = String(process.env.AIFY_SESSION_HANDLE || "");
    const cleanSessionHandle = /\$\{.*\}/.test(rawSessionHandle) ? "" : rawSessionHandle;
    const envHandle = String(cleanSessionHandle || defaultSessionHandleForRuntime(runtime) || "").trim();
    // Plan 6 A2: discover authoritative, env fallback. See computeInitialSessionHandle above.
    const initialHandle = await computeInitialSessionHandle({ adapter: __runtimeAdapter, envHandle });
    let codexLiveBinding = null;
    if (runtime === "codex" && !hasCodexLiveAppServer(runtimeConfig)) {
      codexLiveBinding = await discoverCodexLiveBinding({ sessionHandle: initialHandle, cwd });
      if (codexLiveBinding?.runtimeConfig) runtimeConfig = { ...runtimeConfig, ...codexLiveBinding.runtimeConfig };
    }
    const discoveredCodexThreadId =
      runtime === "codex" && hasCodexLiveAppServer(runtimeConfig)
        ? (codexLiveBinding?.threadId || await discoverCodexLiveThreadId(runtimeConfig, cwd))
        : "";
    const sessionHandle = initialHandle || discoveredCodexThreadId || "";
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
    const channelsEnabled = String(process.env.AIFY_CHANNELS_ENABLED || "").trim() === "1";
    const capabilities = defaultCapabilitiesForRuntime(runtime, resolvedSessionMode, sessionHandle, runtimeConfig);
    const effectiveRuntimeConfig = channelsEnabled
      ? { ...(runtimeConfig || {}), channelEnabled: true }
      : (runtimeConfig || {});
    const payload = {
      agentId: AIFY_AGENT_ID,
      role: AIFY_AGENT_ROLE || "coder",
      name: process.env.AIFY_AGENT_NAME || AIFY_AGENT_ID,
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
      // Phase 4 race guard escape hatch (2026-05-31): when a same-mode resident
      // bridge is still LIVE, the service hard-rejects (409) a different bridge
      // re-registering this identity. Set AIFY_FORCE_REGISTER=1 to deliberately
      // take over after restarting the prior wrapper.
      force: String(process.env.AIFY_FORCE_REGISTER || "").trim() === "1",
    };
    try {
      const r = await httpCall("POST", "/agents", payload);
      let runtimeState = {};
      try {
        const agentInfo = await httpCall("GET", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}`);
        runtimeState = agentInfo.agent?.runtimeState || {};
      } catch {
        // Best effort.
      }
      // WHOSE ANSWER THIS IS. `runtimeState.bridgeInstanceId` names the bridge that OWNS this agent,
      // and for a managed agent that is the environment bridge hosting its delivery loop -- not this
      // per-session sidecar. Writing it here made the field a race between two processes with two
      // meanings, and `aify-comms doctor` then reported a live, answering agent as an orphaned loop
      // "bound to no live bridge", advising a bridge relaunch that reaps the fleet.
      // No `isEnvironmentBridge`: this function returns above when that is what we are, and a gate
      // in this suite refuses a second reference to a marker Phase 8 is retiring.
      const ownership = mayClaimEnvironmentOwnership({
        sessionMode: resolvedSessionMode,
        managedWrapperChild: payload.managedWrapperChild === true,
      });
      // NO `pendingTakeover` TERM. It read `r.ownershipTransition === "pending_resident_takeover"`
      // or a `runtimeState.pendingResidentTakeover` naming this bridge, and the service retired both
      // in `e3c3ce8c` (2026-05-26): every remaining mention of that key in the service is a `pop`,
      // and the transition vocabulary is `console_terminal_attached` / `manual_switch_required`. So
      // the term was always false and this read `if (ownership.claim)`. Parking a resident that
      // registers against a driving managed agent is the SERVICE's job now -- the
      // `manualResidentCandidate` path returns `manual_switch_required` and never lets it drive.
      if (ownership.claim) {
        runtimeState = { ...runtimeState, bridgeInstanceId: BRIDGE_INSTANCE_ID };
        try {
          await httpCall("PATCH", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/runtime-state`, { runtimeState });
        } catch {
          // Best effort.
        }
      }
      REMOTE_AGENT_STATE.set(AIFY_AGENT_ID, { info: { ...payload, runtimeState } });
      try {
        writeAgentBindingFile({ pid: process.ppid || process.pid, agentId: AIFY_AGENT_ID, bridgeId: BRIDGE_INSTANCE_ID });
      } catch {
        // Best effort; notification hooks can still operate after explicit comms_register.
      }
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
            autoRegisterConfiguredAgent(retriesLeft - 1).catch(() => {});
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
    runtimeConfig: info.runtimeConfig || {},
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
