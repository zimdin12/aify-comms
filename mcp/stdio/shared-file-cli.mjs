#!/usr/bin/env node
// The two local file tools (`shared-file-transfer.mjs`) for a runtime that holds no comms MCP server of its own:
// hermes on an HTTP-tools launch (0.9.11), through its plugin (integrations/hermes-aify-plugin/.../file_tools.py).
//
//   node shared-file-cli.mjs --list                  the tools, as hermes schemas ({name, description, parameters})
//   node shared-file-cli.mjs <tool> '<json args>'    run one as AIFY_AGENT_ID; prints its text, exit 1 on its error
//
// The same code the Claude channel serves these tools with, and the same endpoint and key resolution
// (`aify-service-endpoint.mjs`): the environment first, then the credential aify-env holds.

import { pathToFileURL } from "node:url";

import { SERVER_URL, keyForUrl } from "./aify-service-endpoint.mjs";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
import { SHARED_FILE_TOOLS, callSharedFileTool } from "./shared-file-transfer.mjs";

/** PURE. The tools as hermes' plugin registry takes them. */
export function hermesSchemas(tools = SHARED_FILE_TOOLS) {
  return tools.map(({ name, description, inputSchema }) => ({ name, description, parameters: inputSchema }));
}

async function runSharedFileCli([tool, json = "{}"]) {
  if (tool === "--list") return { text: JSON.stringify(hermesSchemas()), isError: false };
  if (!SERVER_URL) return { text: "No aify-comms service URL is configured (AIFY_SERVER_URL).", isError: true };
  let args;
  try { args = JSON.parse(json); } catch { return { text: `The arguments are not JSON: ${json.slice(0, 80)}`, isError: true }; }
  const result = await callSharedFileTool(tool, args, { agentId: AIFY_AGENT_ID, serverUrl: SERVER_URL, apiKey: keyForUrl(SERVER_URL) });
  return { text: result.content[0].text, isError: Boolean(result.isError) };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { text, isError } = await runSharedFileCli(process.argv.slice(2));
  process.stdout.write(`${text}\n`);
  process.exitCode = isError ? 1 : 0;
}
