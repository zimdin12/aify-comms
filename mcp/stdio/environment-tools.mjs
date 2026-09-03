// Environments, and starting an agent in one.
//
// Two MCP tools — `comms_envs` lists the environments the hub knows about, `comms_spawn` starts a new
// managed agent in one — plus the one helper that renders an environment line for both. v0.5.4 layer 2 of
// the server.js decomposition, the fifth tool group to move.
//
// `summarizeEnvironment` IS PRIVATE HERE, and that is the reviewer's rule rather than an oversight: a
// group leaf exports its owner surface and keeps helpers with no consumer outside the group. Its two
// callers are the two tools below and nothing else, so it travels with them and its output is asserted
// through them.
//
// WHY THEY ARE ONE SUBJECT. `comms_spawn` cannot be used without knowing which environments exist, and it
// says so: when a spawn fails for want of a valid environment its error renders the available ones with
// the SAME helper `comms_envs` uses. Splitting them would put that shared rendering in one module and its
// other caller in another, and the failure would be two lists that drift apart — a caller told one thing
// by the listing and another by the error.
//
// WHAT THESE TOOLS SAY ABOUT LIVENESS, and why it is not `status`. `status` and `lastSeen` are
// refreshed by aify-env ADVERTISING the host; a spawn needs something willing to CLAIM the
// request, which the service reads from `metadata.bridgeLastSeen`. Measured 2026-09-02: this
// listing rendered `windows:StevenZ-L:default [online]` while `/spawn` returned 409 in the same
// minute, and an agent that correctly trusted the listing reported the fleet ready and was
// refused six times. The tool an agent consults before acting is the worst place to answer from
// the wrong field, so both tools ask `spawn-claimer.mjs` -- the module the doctor asks, and the
// one whose freshness constant is gated against the service's own.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { normalizeRuntime } from "./runtimes.js";
import { validateName } from "./safe-name.mjs";
// The SAME predicate `/spawn` is gated on, and the same one the doctor asks. Three instruments
// reading three different fields is what produced the failure described above.
import {
  BRIDGE_STAMP_ABSENT,
  BRIDGE_STAMP_FRESH,
  BRIDGE_STAMP_INVALID,
  BRIDGE_STAMP_STALE,
  bridgeStampAgeAt,
  bridgeStampStateAt,
} from "./spawn-claimer.mjs";

/**
 * Can a spawn be CLAIMED here, in the words the reader has to act on.
 *
 * FOUR ANSWERS, NOT A BOOLEAN, because the failing states mean different things to whoever reads
 * this. STALE is a bridge that stopped, and its AGE is the fact that tells an operator whether to
 * restart something. ABSENT is genuinely UNKNOWN from the host side -- the service resolves it
 * against `bridge_instances`, a table no endpoint exposes -- so the honest answer is that the
 * spawn attempt is the authority. Calling that ready would rebuild the defect in the header note;
 * calling it dead would refuse an environment that works.
 */
function claimability(env, now) {
  switch (bridgeStampStateAt(env, now)) {
    case BRIDGE_STAMP_FRESH:
      return { canSpawn: true, note: "can spawn" };
    case BRIDGE_STAMP_STALE:
      return { canSpawn: false, note: `CANNOT SPAWN: no bridge since ${bridgeStampAgeAt(env, now)}` };
    case BRIDGE_STAMP_INVALID:
      return { canSpawn: false, note: "CANNOT SPAWN: unreadable bridge timestamp" };
    case BRIDGE_STAMP_ABSENT:
    default:
      return { canSpawn: false, unknown: true, note: "spawn UNPROVEN: no bridge stamp on this row" };
  }
}

function summarizeEnvironment(env, now = Date.now()) {
  const runtimes = (env.runtimes || []).map((item) => item.runtime).filter(Boolean).join(", ") || "no runtimes";
  const roots = (env.cwdRoots || []).join(", ") || "no roots";
  // The CLAIM answer LEADS, because it is the one the reader is about to act on. `status` stays,
  // named as what it is, so the two are never again read as the same fact.
  return `- ${env.id} [${claimability(env, now).note}] ${env.label || ""}\n  advertised: ${env.status || "unknown"}; ${env.os || "unknown"}/${env.kind || "unknown"}; runtimes: ${runtimes}; roots: ${roots}`;
}

// Registers the two environment tools on an MCP server. A function rather than a module-scope side
// effect, so a fake server can capture the registrations and a test can call the handlers without an MCP
// transport. `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The two bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerEnvironmentTools(server, z) {
  server.tool(
    "comms_envs",
    "List environment bridges. Use before spawning a persistent managed agent to pick the host, runtime and workspace root. The bracket says whether a spawn can be CLAIMED there, which is a different fact from the advertised status on the second line: an environment can be advertised and still have nothing able to run anything.",
    {},
    async () => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Environment-backed spawn requires remote server mode. Start aify-comms against the dashboard service first." }], isError: true };
      }
      const r = await httpCall("GET", "/environments");
      const envs = r.environments || [];
      if (!envs.length) return { content: [{ type: "text", text: "No environment bridges are connected. Start `aify-comms` in WSL/Linux and/or `aify-comms.cmd` in Windows." }] };
      // ONE CLOCK FOR THE WHOLE LISTING, and passed EXPLICITLY. `envs.map(summarizeEnvironment)`
      // hands the renderer the ARRAY INDEX as its second argument, so every row was aged against
      // `now = 0` and read as a corrupt timestamp. It was written that way here and caught by the
      // first test that asserted the rendered bracket -- a default parameter plus a bare `map` is
      // a defect with no syntax error in it.
      const now = Date.now();
      return { content: [{ type: "text", text: `${envs.length} environment(s):\n${envs.map((e) => summarizeEnvironment(e, now)).join("\n")}` }] };
    }
  );

  server.tool(
    "comms_spawn",
    "Create a persistent dashboard-managed agent session through an environment bridge. The only normal agent-spawn path; choose an environment from comms_envs, or omit environmentId to take the first that can claim a spawn for the runtime.",
    {
      from: z.string().describe("Owning/manager agent ID"),
      environmentId: z.string().optional().describe("Environment ID from comms_envs. If omitted, the first environment able to claim a spawn for this runtime is used."),
      agentId: z.string().describe("Stable agent ID to create"),
      role: z.string().describe("Agent role: manager, coder, reviewer, tester, researcher, architect, operator"),
      runtime: z.string().describe("Runtime for the persistent agent session: codex, claude-code, hermes, opencode, or pi"),
      workspace: z.string().optional().describe("Workspace path inside the selected environment's advertised roots"),
      name: z.string().optional().describe("Friendly name"),
      model: z.string().optional().describe("Preferred model/profile value"),
      instructions: z.string().optional().describe("Standing instructions for the agent"),
      initialMessage: z.string().optional().describe("Initial task/brief to deliver after spawn"),
      subject: z.string().optional().describe("Initial task subject"),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Priority for the initial task"),
    },
    async ({ from, environmentId, agentId, role, runtime, workspace, name, model, instructions, initialMessage, subject, priority }) => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Environment-backed spawn requires remote server mode. Start aify-comms against the dashboard service first." }], isError: true };
      }
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
      const resolvedRuntime = normalizeRuntime(runtime || "generic");
      const envs = (await httpCall("GET", "/environments")).environments || [];
      const now = Date.now();
      const runs = (item) => (item.runtimes || []).some((r) => normalizeRuntime(r.runtime || "") === resolvedRuntime);
      const advertised = (item) => String(item.status || "").toLowerCase() === "online";
      // AUTO-SELECTION PREFERS A PROVEN CLAIMER, and both conditions are the service's, in the
      // service's order: `/spawn` refuses a non-`online` row first, then refuses one with no live
      // bridge. This used to stop at the first, which is only aify-env saying the host exists -- so
      // it picked hosts where nothing could claim, and the request queued for ever with no error
      // anywhere. An UNPROVEN row is still a candidate, second: the host cannot read
      // `bridge_instances`, so refusing there would refuse environments that do work.
      let env = environmentId
        ? envs.find((item) => item.id === environmentId)
        : envs.find((item) => advertised(item) && runs(item) && claimability(item, now).canSpawn)
          || envs.find((item) => advertised(item) && runs(item) && claimability(item, now).unknown);
      if (!env) {
        const hint = envs.length ? `Available environments:\n${envs.map((e) => summarizeEnvironment(e, now)).join("\n")}` : "No environment bridges are connected.";
        return { content: [{ type: "text", text: `No matching environment found for runtime "${resolvedRuntime}".\n${hint}` }], isError: true };
      }
      // THE SERVICE'S TWO GATES, IN ITS ORDER, so this tool predicts the answer rather than
      // guessing at it. First: is the row advertised at all.
      if (!advertised(env)) {
        return { content: [{ type: "text", text: `Environment "${env.id}" is ${env.status || "unknown"}, not online. Start its bridge first.` }], isError: true };
      }
      // Second, and the one that was missing: can anything CLAIM here. An advertised row means
      // aify-env described the host; claiming is a separate capability, and reading the first as
      // the second is what let this tool report a fleet ready that was refused six times. Refusing
      // here is also more legible than the service's 409, because it carries the AGE -- "no bridge
      // since 26h ago" tells an operator to restart something, where a 409 says only no.
      const claim = claimability(env, now);
      if (!claim.canSpawn && !claim.unknown) {
        return { content: [{ type: "text", text: `Environment "${env.id}" ${claim.note}. It is advertised as ${env.status || "unknown"}, which is aify-env describing the host rather than offering to run anything -- a different fact, and the one that misled callers before this check existed. Start a claimer on that host and retry.` }], isError: true };
      }
      const supportsRuntime = (env.runtimes || []).some((runtimeInfo) => normalizeRuntime(runtimeInfo.runtime || "") === resolvedRuntime);
      if (!supportsRuntime) {
        return { content: [{ type: "text", text: `Environment "${env.id}" does not advertise runtime "${resolvedRuntime}".` }], isError: true };
      }
      const selectedWorkspace = workspace || (env.cwdRoots || [])[0] || "";
      const r = await httpCall("POST", "/spawn-requests", {
        createdBy: from,
        environmentId: env.id,
        agentId,
        role,
        name,
        runtime: resolvedRuntime,
        workspace: selectedWorkspace,
        model: model || "",
        instructions: instructions || "",
        initialMessage: initialMessage || "",
        subject: subject || (initialMessage ? `Brief ${agentId}` : ""),
        priority: priority || "normal",
        mode: "managed-warm",
        resumePolicy: "native_first",
      });
      const req = r.spawnRequest || {};
      return {
        content: [{
          type: "text",
          text:
            `Queued persistent agent "${agentId}" in ${env.id} (${resolvedRuntime}, ${selectedWorkspace || "default workspace"}). ` +
            `Spawn request: ${req.id || "unknown"} [${req.status || "queued"}].`,
        }],
      };
    }
  );
}
