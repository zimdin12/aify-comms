// Every MCP tool this bridge exposes, registered in one place. Extracted from server.js in v0.5.4.
//
// The list IS the bridge's public surface: an agent can call exactly what is registered here and
// nothing else. A tool dropped from it does not fail loudly — the tool simply stops existing, and the
// agent that reaches for it gets "unknown tool" from the MCP layer with no hint that it used to work.
// That is why the test beside this counts the registrations rather than trusting the file to look right.
//
// `server` and `z` are parameters rather than imports because server.js owns the McpServer instance and
// the schema library; `ensureDispatchLoop` stays there too, and is passed through to the one tool that
// needs to start dispatch after a registration.

import { registerAgentReportingTools } from "./agent-reporting-tools.mjs";
import { registerArtifactTools } from "./artifact-tools.mjs";
import { registerChannelTools } from "./channel-tools.mjs";
import { registerCompactTool } from "./compact-tool.mjs";
import { registerConsoleTools } from "./console-tools.mjs";
import { registerDashboardTool } from "./dashboard-tool.mjs";
import { registerDispatchTools } from "./dispatch-tools.mjs";
import { registerEnvironmentTools } from "./environment-tools.mjs";
import { registerInboxTools } from "./inbox-tools.mjs";
import { registerLifecycleTools } from "./lifecycle-tools.mjs";
import { registerRegistrationTool } from "./registration-tool.mjs";
import { registerSearchTool } from "./search-tool.mjs";
import { registerSelfRecordTools } from "./self-record-tools.mjs";
import { registerSendTools } from "./send-tools.mjs";
import { registerUsageTool } from "./usage-tool.mjs";

export function registerAllTools(server, z, { ensureDispatchLoop }) {

  // ═══════════════════════════════════════════════════════════════════════════════
  // 1. comms_register -- Register agent with ID, role, name, cwd, model, instructions
  // ═══════════════════════════════════════════════════════════════════════════════

  registerRegistrationTool(server, z, { ensureDispatchLoop });

  registerEnvironmentTools(server, z);






  registerUsageTool(server, z);


  registerCompactTool(server, z);

  registerAgentReportingTools(server, z);

  registerSelfRecordTools(server, z);

  // ═══════════════════════════════════════════════════════════════════════════════
  // 3. comms_send -- Send message to agent by ID or role
  // ═══════════════════════════════════════════════════════════════════════════════

  // COMMS_SEND_TOOL_DESCRIPTION moved to ./send-tools.mjs in v0.5.4 — it describes comms_send,
  // so it belongs with the tool rather than with the file the tool used to live in.

  // The two SEND tools live in ./send-tools.mjs. No `moved to` marker: that form names a DECLARATION, and
  // a tool name is not one — every earlier tool extraction left only its register call, same as this.
  registerSendTools(server, z);

  registerDispatchTools(server, z);

  // ═══════════════════════════════════════════════════════════════════════════════
  // comms_console_tail / comms_console_input -- read & unstick a managed agent's console
  // ═══════════════════════════════════════════════════════════════════════════════

  registerConsoleTools(server, z);

  // comms_run_steer removed from stdio — ordinary comms_send does not require
  // knowing the runId, creates an inbox message, and steers busy steer-capable
  // targets unless queueIfBusy=true. Busy non-steer targets queue/merge instead.

  /**
   * Spawn a local runtime instance to handle a triggered message.
   * Fire-and-forget: the result is delivered back to the sender's inbox.
   */
  // spawnTriggeredAgent moved to ./spawn-triggered-agent.mjs in v0.5.4.

  registerInboxTools(server, z);

  registerSearchTool(server, z);




  // ═══════════════════════════════════════════════════════════════════════════════
  // 6. comms_share -- Share text content or file to shared space
  // ═══════════════════════════════════════════════════════════════════════════════

  registerArtifactTools(server, z);

  registerChannelTools(server, z);


  // ═══════════════════════════════════════════════════════════════════════════════
  // 11. comms_channel_send -- Send message to channel
  // ═══════════════════════════════════════════════════════════════════════════════




  registerLifecycleTools(server, z);

  registerDashboardTool(server, z);

  // ── Entrypoint ───────────────────────────────────────────────────────────────
}
