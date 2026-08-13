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
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { normalizeRuntime } from "./runtimes.js";
import { validateName } from "./safe-name.mjs";

function summarizeEnvironment(env) {
  const runtimes = (env.runtimes || []).map((item) => item.runtime).filter(Boolean).join(", ") || "no runtimes";
  const roots = (env.cwdRoots || []).join(", ") || "no roots";
  return `- ${env.id} [${env.status || "unknown"}] ${env.label || ""}\n  ${env.os || "unknown"}/${env.kind || "unknown"}; runtimes: ${runtimes}; roots: ${roots}`;
}

// Registers the two environment tools on an MCP server. A function rather than a module-scope side
// effect, so a fake server can capture the registrations and a test can call the handlers without an MCP
// transport. `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The two bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerEnvironmentTools(server, z) {
  server.tool(
    "comms_envs",
    "List connected environment bridges. Use this before spawning persistent managed agents so you can choose the right host, runtime, and workspace root.",
    {},
    async () => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Environment-backed spawn requires remote server mode. Start aify-comms against the dashboard service first." }], isError: true };
      }
      const r = await httpCall("GET", "/environments");
      const envs = r.environments || [];
      if (!envs.length) return { content: [{ type: "text", text: "No environment bridges are connected. Start `aify-comms` in WSL/Linux and/or `aify-comms.cmd` in Windows." }] };
      return { content: [{ type: "text", text: `${envs.length} environment(s):\n${envs.map(summarizeEnvironment).join("\n")}` }] };
    }
  );

  server.tool(
    "comms_spawn",
    "Create a persistent dashboard-managed agent session through an environment bridge. This is the only normal agent-spawn path; choose an environment from comms_envs or omit environmentId to use the first online environment supporting the runtime.",
    {
      from: z.string().describe("Owning/manager agent ID"),
      environmentId: z.string().optional().describe("Environment ID from comms_envs. If omitted, first online environment supporting runtime is used."),
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
      let env = environmentId
        ? envs.find((item) => item.id === environmentId)
        : envs.find((item) =>
            String(item.status || "").toLowerCase() === "online" &&
            (item.runtimes || []).some((runtimeInfo) => normalizeRuntime(runtimeInfo.runtime || "") === resolvedRuntime)
          );
      if (!env) {
        const hint = envs.length ? `Available environments:\n${envs.map(summarizeEnvironment).join("\n")}` : "No environment bridges are connected.";
        return { content: [{ type: "text", text: `No matching environment found for runtime "${resolvedRuntime}".\n${hint}` }], isError: true };
      }
      if (String(env.status || "").toLowerCase() !== "online") {
        return { content: [{ type: "text", text: `Environment "${env.id}" is ${env.status || "unknown"}, not online. Start its bridge first.` }], isError: true };
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
