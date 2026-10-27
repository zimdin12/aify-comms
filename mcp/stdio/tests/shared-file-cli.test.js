// The two local file tools as hermes' plugin registers them (0.9.11): the schemas come from the one definition the
// Claude channel lists, so a hermes agent sees the same arguments. The plugin's end-to-end transfer is executed in
// service/tests/test_hermes_shares_files_through_its_plugin.py.

import assert from "node:assert/strict";
import { test } from "node:test";

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const { hermesSchemas } = await import("../shared-file-cli.mjs");
const { SHARED_FILE_TOOLS } = await import("../shared-file-transfer.mjs");

test("hermesSchemas gives every file tool its name, description and the channel's input schema as parameters", () => {
  assert.deepEqual(hermesSchemas(), SHARED_FILE_TOOLS.map((t) => ({ name: t.name, description: t.description, parameters: t.inputSchema })));
  assert.deepEqual(hermesSchemas().map((t) => t.parameters.required), [["filePath"], ["name"]]);
});

test("with no service URL the file tools say so and fetch nothing", async () => {
  const { callSharedFileTool } = await import("../shared-file-transfer.mjs");
  const { NO_SERVICE_URL } = await import("../aify-service-endpoint.mjs");
  let fetched = 0;
  const res = await callSharedFileTool("comms_save_file", { name: "a.zip" }, { agentId: "a", serverUrl: "", fetchImpl: async () => { fetched += 1; } });
  assert.deepEqual([res.content[0].text, res.isError, fetched], [NO_SERVICE_URL, true, 0]);
});
