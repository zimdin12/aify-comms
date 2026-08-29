// `comms_register` — how an agent tells this bridge, and the service, who it is.
//
// The largest single tool in the bridge at 302 lines, and the last one to move. It was not blocked by its
// own complexity: measured at v0.5.4 its closure was 39 functions and 1,381 lines, of which ONE call —
// `ensureDispatchLoop` — accounted for 34 functions and 1,239 of them. Everything that call drags, it drags
// through `runDispatchLoop`, which has its own open packet and must not travel as one body. Five owner
// moves later (gateway config, detector state, heartbeat fields, active-run reconciliation, registration
// inputs) the residual closure is exactly that one function and zero mutable module state.
//
// SO THE DISPATCH LOOP IS INJECTED RATHER THAN IMPORTED, on the precedent already set for `z`: a
// caller-supplied dependency when importing would drag something structural. The distinction this preserves
// is worth stating, because it is the reason the shape is allowed at all — registration's relationship to
// the dispatch loop is "tell it to exist", not "own it". This module does not know the poll cadence, does
// not hold the timer, does not decide when the loop stops, and cannot start a second one. A registration
// module that owned the loop would own the bridge; one that asks for it owns registration.
//
// WHAT REGISTRATION ACTUALLY DECIDES, none of which is a formality:
//   * the agent's IDENTITY and cwd — `registration-inputs.mjs` resolves those, and a wrong cwd produces an
//     agent that registers cleanly and cannot be dispatched to, because the marker key is a hash of it;
//   * its CAPABILITIES and session handle, which decide whether a message to it steers, queues, or is
//     dropped;
//   * whether this process must HOST the dispatch loop — a local agent with no environment bridge polling
//     for it would sit with queued work and never run it;
//   * for claude, LATE ARMING of the turn-end detector: a bridge whose wrapper did not export
//     `AIFY_AGENT_ID` starts with no identity, so the boot-time arm no-ops and this is the only remaining
//     chance to arm it. Miss it and the agent's turns never report as ending.
//
// RE-REGISTRATION IS A FULL STATE REFRESH, not a merge — everything except `description` is replaced. That
// is a deliberate project-wide rule (see DECISIONS.md), and it is why the resolvers this calls delete stale
// keys rather than carrying them forward.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { mayClaimEnvironmentOwnership } from "./environment-ownership-claim.mjs";
import fs from "fs";
import path from "path";

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { writeAgentBindingFile } from "./binding-file.js";
import { ACTIVE_RUNS, REMOTE_AGENT_STATE } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID, BRIDGE_STARTED_AT } from "./bridge-instance.mjs";
import { armClaudeTurnEndDetector, isClaudeTurnDetectorArmed } from "./claude-turn-detector-state.mjs";
import { writeSessionIdMarker } from "./hermes-endpoint.js";
import { IS_MANAGED_DISPATCH } from "./launch-identity.mjs";
import { reconcileLocalActiveRun } from "./local-active-run.mjs";
import { INBOX_DIR, readAgents, writeAgents } from "./local-store.mjs";
import { fillSessionHandleFromAdapter } from "./register-helpers.js";
import { residentIdentityWarning } from "./register-identity.js";
import {
  DEFAULT_CWD,
  claimCapturedClaudeSession,
  normalizeRegistrationCwd,
  resolvedRuntimeConfigForRegistration,
} from "./registration-inputs.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";
import {
  defaultCapabilitiesForRuntime,
  defaultSessionHandleForRuntime,
  defaultMachineId,
  detectRuntime,
  discoverCodexLiveBinding,
  discoverCodexLiveThreadId,
  hasCodexLiveAppServer,
} from "./runtimes.js";
import { validateName } from "./safe-name.mjs";
import { normalizeSessionMode } from "./session-mode.mjs";

// Bound from the one `defaultMachineId()` in `runtimes.js`, as six other bridge modules already do. It is a
// pure function of env and hostname — deterministic across processes — so deriving it here is not the
// duplicate-state defect that kept `BRIDGE_STARTED_AT` and `DEFAULT_CWD` in single owners. Re-deriving a
// pure function is safe; re-reading a clock or a cwd is not.
const MACHINE_ID = defaultMachineId();

// `ensureDispatchLoop` is injected. See the header: this module asks for the loop, it does not own it.
export function registerRegistrationTool(server, z, { ensureDispatchLoop }) {
  server.tool(
    "comms_register",
    "Register this agent instance. " +
      "Register this exact live session so other agents can message and, when supported, trigger this specific session. " +
      "New persistent agents should be created with comms_spawn or the dashboard Environments page.",
    {
      agentId: z.string().describe("Unique ID (e.g. 'coder-1', 'tester')"),
      role: z.string().describe("Role: 'coder', 'tester', 'reviewer', 'architect', etc."),
      name: z.string().optional().describe("Friendly name"),
      cwd: z.string().optional().describe("Working directory (used when triggered)"),
      model: z.string().optional().describe("Preferred model (e.g. 'sonnet', 'opus', 'haiku')"),
      description: z.string().optional().describe("Team-facing short description: who you are, what project you're on, what you focus on. Visible to other agents in comms_agents. Preserved across re-register; pass \"\" to clear."),
      instructions: z.string().optional().describe("Standing instructions for when triggered"),
      runtime: z.string().optional().describe("Runtime type (e.g. 'claude-code', 'codex', 'hermes', 'opencode', 'pi')"),
      machineId: z.string().optional().describe("Stable machine identifier (auto-detected by default)"),
      launchMode: z.string().optional().describe("Launch mode hint (default: detached)"),
      sessionMode: z.enum(["resident", "managed"]).optional().describe("Session type (default: resident)"),
      sessionHandle: z.string().optional().describe("Runtime-specific live session handle if known"),
      appServerUrl: z.string().optional().describe("Runtime-specific live app-server URL if known (Codex live sessions)"),
      managedBy: z.string().optional().describe("Owning agent ID for environment-managed sessions"),
    },
    async (args) => {
      args = await fillSessionHandleFromAdapter(args, __runtimeAdapter);
      const { agentId, role, name, cwd, model, description, instructions, runtime, machineId, launchMode, sessionMode, sessionHandle, appServerUrl, managedBy } = args;
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
      if (IS_MANAGED_DISPATCH) {
        // Allow EXPLICIT resident takeover. Operator-verified 2026-05-22:
        // a managed dashboard agent that's stopped/stale (wake disabled)
        // needs a way to be picked up by a fresh CLI session — without
        // this exit, the operator's only path was to delete + re-register
        // from a different shell, which fights the env-var lifecycle.
        // The guard's original purpose was to prevent ACCIDENTAL conversion
        // from a managed-dispatch turn's tool-call. An explicit
        // `sessionMode: "resident"` is an intentional act and should
        // succeed. Other sessionMode values (managed, omitted, etc.)
        // still hit the guard so a tool-call slip can't reclassify.
        //
        // TESTS EXPLICITNESS, NOT THE NORMALIZED DEFAULT, and that distinction is the whole guard.
        // `normalizeSessionMode` fails toward "resident" by design — an unreadable mode must never yield a
        // session the bridge may reap — so `normalizeSessionMode(undefined) === "resident"` and this
        // condition was FALSE for an omitted mode. The accidental case the guard exists for, and the one its
        // own error text describes, sailed straight through and converted the managed agent to a resident
        // CLI identity. Open from `9aebbfcc`, which added the takeover hatch inside a previously
        // unconditional guard, until a real-handler test replaced the source regex that had been "proving"
        // it. The schema is `z.enum(["resident", "managed"]).optional()`, so comparing the RAW value is
        // exact: omitted and "managed" are refused, explicit "resident" passes.
        if (sessionMode !== "resident") {
          return {
            content: [{
              type: "text",
              text:
                "This is a dashboard-managed run. The agent identity is already registered by the environment bridge, " +
                "so comms_register without an explicit sessionMode is disabled here to avoid converting the managed agent into a resident CLI identity. " +
                "To take this identity over as a resident CLI session (e.g., the managed worker is stopped and you want to claim it from here), " +
                "call comms_register with sessionMode=\"resident\" explicitly. " +
                "Otherwise, do NOT call comms_register from this managed run — reply to the current aify-comms message with " +
                "comms_send(type=\"response\", inReplyTo=<the message id>) when a reply is owed (final plain text is only your working output, not the delivered reply).",
            }],
            isError: true,
          };
        }
      }
      const resolvedRuntime = detectRuntime(runtime);
      const resolvedMachineId = machineId || MACHINE_ID;
      const resolvedSessionMode = normalizeSessionMode(sessionMode);
      const previousInfo = REMOTE_AGENT_STATE.get(agentId)?.info;
      const resolvedCwd = normalizeRegistrationCwd(resolvedRuntime, cwd || DEFAULT_CWD);
      let runtimeConfig = resolvedRuntimeConfigForRegistration(resolvedRuntime, previousInfo, resolvedCwd);
      const hermesGatewayRegistration =
        resolvedRuntime === "hermes" &&
        /^wss?:\/\//i.test(String(runtimeConfig?.gatewayUrl || ""));
      const allowPreviousSessionHandle =
        !(hermesGatewayRegistration && !String(sessionHandle || "").trim());
      const initialSessionHandle =
        sessionHandle ||
        defaultSessionHandleForRuntime(resolvedRuntime) ||
        (allowPreviousSessionHandle ? previousInfo?.sessionHandle : "") ||
        "";
      const explicitAppServerUrl = String(appServerUrl || "").trim();
      if (resolvedRuntime === "codex" && explicitAppServerUrl) {
        runtimeConfig = { ...runtimeConfig, appServerUrl: explicitAppServerUrl };
      }
      let codexLiveBinding = null;
      if (resolvedRuntime === "codex" && !hasCodexLiveAppServer(runtimeConfig)) {
        codexLiveBinding = await discoverCodexLiveBinding({
          sessionHandle: initialSessionHandle,
          cwd: resolvedCwd,
        });
        if (codexLiveBinding?.runtimeConfig) {
          runtimeConfig = { ...runtimeConfig, ...codexLiveBinding.runtimeConfig };
        }
      }
      const discoveredCodexThreadId =
        resolvedRuntime === "codex" && hasCodexLiveAppServer(runtimeConfig)
          ? (codexLiveBinding?.threadId || await discoverCodexLiveThreadId(runtimeConfig, resolvedCwd))
          : "";
      const resolvedSessionHandle =
        sessionHandle ||
        discoveredCodexThreadId ||
        initialSessionHandle ||
        (allowPreviousSessionHandle ? previousInfo?.sessionHandle : "") ||
        "";
      // Native-session-id model (2026-06-03): comms_register binds this agent's
      // identity to its REAL hermes session id by persisting the per-agent marker,
      // so a relaunch resumes the SAME session and the delivery loop targets it.
      // Best-effort; never throws. (Gateway-url resolution is unchanged.)
      const resolvedAgentId = String(args?.agentId || agentId || "").trim();
      if (resolvedRuntime === "hermes" && resolvedAgentId && resolvedSessionHandle) {
        try { writeSessionIdMarker(resolvedAgentId, resolvedSessionHandle); } catch { /* best-effort */ }
      }
      const capabilities = defaultCapabilitiesForRuntime(resolvedRuntime, resolvedSessionMode, resolvedSessionHandle, runtimeConfig);

      const agentData = {
        agentId,
        role,
        name,
        cwd: resolvedCwd,
        model: model || "",
        description: description === undefined ? null : description,
        instructions: instructions || "",
        runtime: resolvedRuntime,
        machineId: resolvedMachineId,
        launchMode: launchMode || "detached",
        sessionMode: resolvedSessionMode,
        sessionHandle: resolvedSessionHandle,
        managedBy: managedBy || "",
        bridgeId: BRIDGE_INSTANCE_ID,
        capabilities,
        runtimeConfig,
        restoreDeleted: true,
        // Tombstone-resurrection guard (2026-06-03): carry this bridge's launch
        // time so the service can distinguish a genuine fresh relaunch from a
        // lingering bridge re-registering a deliberately-removed agent.
        bridgeStartedAt: BRIDGE_STARTED_AT,
      };

      // Write agent ID to a session-specific temp file keyed by PID so the
      // channel bridge and notification hook can find it. Only resident
      // sessions represent the current UI/CLI session.
      //
      // Previously we also wrote to {cwd}/.aify-agent, but that file is
      // shared across all sessions in the same directory — when two agents
      // (e.g. manager + tester) run in the same folder, the last to
      // register wins and the other agent's channel bridge picks up the
      // wrong agentId, causing cross-talk.
      if (resolvedSessionMode === "resident") {
        try {
          writeAgentBindingFile({ pid: process.ppid || process.pid, agentId, bridgeId: BRIDGE_INSTANCE_ID });
        } catch { /* best effort */ }
      }

      // REGISTERING TURNS STATUS ON (2026-07-14). If the wrapper never exported
      // AIFY_AGENT_ID (session launched without `--aify-agent`), the turn detector could not
      // arm at boot and this bridge had NO way to ever report turn state — the agent would
      // register, message and heartbeat perfectly while its status latched forever. But THIS
      // call is the bridge learning who it is, so use it: claim the session id the hook
      // captured before we had an identity, then arm the detector. Registering now does what
      // an operator always assumed it did.
      if (!isClaudeTurnDetectorArmed() && agentId) {
        const claimed = claimCapturedClaudeSession(agentId);
        if (armClaudeTurnEndDetector(agentId)) {
          console.error(
            `[aify] turn detection armed late for '${agentId}' via comms_register ` +
            `(session started without --aify-agent${claimed ? "; session id claimed from the pid capture" : ""}). ` +
            `Status will work from now on; relaunch with --aify-agent to arm it at boot.`,
          );
        }
      }

      if (IS_REMOTE) {
        const r = await httpCall("POST", "/agents", agentData);
        let runtimeState = {};
        try {
          const agentInfo = await httpCall("GET", `/agents/${encodeURIComponent(agentId)}`);
          runtimeState = agentInfo.agent?.runtimeState || {};
        } catch {
          // best effort
        }
        // WHOSE ANSWER THIS IS. Same rule as the auto-registration path: the field names the bridge
        // that OWNS this agent, and a managed agent is owned by the environment bridge hosting its
        // delivery loop, not by the sidecar registering it. See environment-ownership-claim.mjs for
        // what two writers of this one field cost.
        // NO `isEnvironmentBridge` ARGUMENT HERE, deliberately. This is the `comms_register` tool, run
        // by an agent's own session; the environment bridge does not register itself through it, and
        // `auto-registration.mjs` returns early for that process anyway. Passing the flag would add a
        // NEW import of the environment-bridge marker, and a gate in this suite refuses that on the
        // grounds that Phase 8 is retiring the command -- correctly, since a coupling site added
        // today is one more to remove later.
        if (mayClaimEnvironmentOwnership({ sessionMode: agentData.sessionMode }).claim) {
          runtimeState = { ...runtimeState, bridgeInstanceId: BRIDGE_INSTANCE_ID };
          try {
            await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
              runtimeState,
            });
          } catch {
            // best effort
          }
        }
        REMOTE_AGENT_STATE.set(agentId, {
          info: {
            ...agentData,
            runtimeState,
          },
        });
        const active = ACTIVE_RUNS.get(agentId);
        if (active) {
          await reconcileLocalActiveRun(agentId, REMOTE_AGENT_STATE.get(agentId), active);
        }
        try {
          const agentsRes = await httpCall("GET", "/agents");
          for (const [managedId, managedInfo] of Object.entries(agentsRes.agents || {})) {
            if (normalizeSessionMode(managedInfo.sessionMode) !== "managed") continue;
            if ((managedInfo.managedBy || "") !== agentId) continue;
            if ((managedInfo.machineId || "") !== resolvedMachineId) continue;
            const managedRuntimeState = { ...(managedInfo.runtimeState || {}), bridgeInstanceId: BRIDGE_INSTANCE_ID };
            try {
              await httpCall("PATCH", `/agents/${encodeURIComponent(managedId)}/runtime-state`, {
                runtimeState: managedRuntimeState,
              });
            } catch {
              // best effort
            }
            REMOTE_AGENT_STATE.set(managedId, {
              info: {
                agentId: managedId,
                role: managedInfo.role,
                name: managedInfo.name,
                cwd: managedInfo.cwd || DEFAULT_CWD,
                model: managedInfo.model || "",
                instructions: managedInfo.instructions || "",
                runtime: managedInfo.runtime || "generic",
                machineId: managedInfo.machineId || resolvedMachineId,
                launchMode: managedInfo.launchMode || "managed",
                sessionMode: managedInfo.sessionMode || "managed",
                sessionHandle: managedInfo.sessionHandle || "",
                managedBy: managedInfo.managedBy || agentId,
                capabilities: managedInfo.capabilities || [],
                runtimeConfig: managedInfo.runtimeConfig || {},
                runtimeState: managedRuntimeState,
              },
            });
            const active = ACTIVE_RUNS.get(managedId);
            if (active) {
              await reconcileLocalActiveRun(managedId, REMOTE_AGENT_STATE.get(managedId), active);
            }
          }
        } catch {
          // best effort
        }
        ensureDispatchLoop();
        return {
          content: [{
            type: "text",
            text:
              `Registered "${r.agentId}" (${resolvedSessionMode}, role: ${r.role}, runtime: ${resolvedRuntime}, machine: ${resolvedMachineId}).` +
              (resolvedSessionHandle ? ` Session: ${resolvedSessionHandle}` : "") +
              // A resident that registered from a session with no AIFY_AGENT_ID is registered but
              // structurally unable to report turns — say so HERE, the one moment the agent is
              // listening. See register-identity.js for why it cannot be fixed after launch.
              residentIdentityWarning({
                registeredAgentId: r.agentId,
                envAgentId: process.env.AIFY_AGENT_ID,
                sessionMode: resolvedSessionMode,
                runtime: resolvedRuntime,
              }) +
              (
                resolvedRuntime === "codex" &&
                hasCodexLiveAppServer(runtimeConfig) &&
                !resolvedSessionHandle
                  ? ` Live Codex app-server detected, but no thread was auto-bound. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID") from that same codex-aify session.`
                  : (
                    resolvedRuntime === "codex" &&
                    codexLiveBinding?.ambiguous
                      ? ` Multiple live codex-aify sessions matched this registration, so aify could not safely auto-bind one. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL") from that same live session.`
                      : ""
                  )
              ),
          }],
        };
      }

      const registry = readAgents();
      registry.agents[agentId] = {
        role,
        name: name || agentId,
        cwd: resolvedCwd,
        model: model || "",
        instructions: instructions || "",
        runtime: resolvedRuntime,
        machineId: resolvedMachineId,
        launchMode: launchMode || "detached",
        sessionMode: resolvedSessionMode,
        sessionHandle: resolvedSessionHandle,
        managedBy: managedBy || "",
        capabilities,
        runtimeConfig,
        runtimeState: registry.agents[agentId]?.runtimeState || {},
        registeredAt: new Date().toISOString(),
        lastSeen: new Date().toISOString(),
      };
      writeAgents(registry);
      fs.mkdirSync(path.join(INBOX_DIR, agentId), { recursive: true });
      return {
        content: [{
          type: "text",
          text:
            `Registered "${agentId}" (${resolvedSessionMode}, role: ${role}, cwd: ${resolvedCwd}, runtime: ${resolvedRuntime}).` +
            (resolvedSessionHandle ? ` Session: ${resolvedSessionHandle}` : "") +
            (
              resolvedRuntime === "codex" &&
              hasCodexLiveAppServer(runtimeConfig) &&
              !resolvedSessionHandle
                ? ` Live Codex app-server detected, but no thread was auto-bound. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID") from that same codex-aify session.`
                : (
                  resolvedRuntime === "codex" &&
                  codexLiveBinding?.ambiguous
                    ? ` Multiple live codex-aify sessions matched this registration, so aify could not safely auto-bind one. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL") from that same live session.`
                    : ""
                )
            ),
        }],
      };
    }
  );
}
