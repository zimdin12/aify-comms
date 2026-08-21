import assert from "node:assert/strict";
import { isAifyCommsMcpToolItem } from "../runtimes.js";

assert.equal(isAifyCommsMcpToolItem("mcpToolCall aify-comms"), true);
assert.equal(isAifyCommsMcpToolItem("mcpToolCall aify-comms/comms_send"), true);
assert.equal(isAifyCommsMcpToolItem("mcpToolCall openmemory/search_memory"), false);
assert.equal(isAifyCommsMcpToolItem("agentMessage"), false);
