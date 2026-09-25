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
  registerRegistrationTool(server, z, { ensureDispatchLoop });
  registerEnvironmentTools(server, z);
  registerUsageTool(server, z);
  registerCompactTool(server, z);
  registerAgentReportingTools(server, z);
  registerSelfRecordTools(server, z);
  registerSendTools(server, z);
  registerDispatchTools(server, z);
  registerConsoleTools(server, z);
  registerInboxTools(server, z);
  registerSearchTool(server, z);
  registerArtifactTools(server, z);
  registerChannelTools(server, z);
  registerLifecycleTools(server, z);
  registerDashboardTool(server, z);
}
