#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { bindingFilePathForPid, readAgentBindingFile, removeAgentBindingFile, writeAgentBindingFile } from "../binding-file.js";
import { tmpDir } from "./_tmpdir.js";

const tmp = tmpDir("aify-binding-file-");
const pid = 424242;
const file = bindingFilePathForPid(pid, tmp);

writeAgentBindingFile({ pid, agentId: "agent-a", bridgeId: "bridge-a", dir: tmp });
assert.deepEqual(readAgentBindingFile({ pid, dir: tmp }), {
  agentId: "agent-a",
  bridgeId: "bridge-a",
  pid,
}, "binding reader should parse structured payloads");

removeAgentBindingFile({ pid, bridgeId: "bridge-b", dir: tmp });
assert.equal(fs.existsSync(file), true, "foreign bridge cleanup must not delete the current binding file");

removeAgentBindingFile({ pid, bridgeId: "bridge-a", dir: tmp });
assert.equal(fs.existsSync(file), false, "owner bridge cleanup should delete its own binding file");

fs.writeFileSync(file, "legacy-agent");
assert.deepEqual(readAgentBindingFile({ pid, dir: tmp }), {
  agentId: "legacy-agent",
  bridgeId: "",
  pid,
}, "reader should remain backward-compatible with legacy plain-text binding files");

// Review of 9f6e1a90: two comms processes under one parent, and the OLDER one's registration answer arrives after
// its successor bound. Its write must not take the binding back, and its exit must leave the successor's alone.
fs.rmSync(file, { force: true });
const [older, younger] = ["2026-10-27T05:00:00.000Z", "2026-10-27T05:00:01.000Z"];
assert.equal(writeAgentBindingFile({ pid, agentId: "agent-a", bridgeId: "successor", startedAt: younger, dir: tmp }), true);
assert.equal(writeAgentBindingFile({ pid, agentId: "agent-a", bridgeId: "retired", startedAt: older, dir: tmp }), false,
  "a late answer to an older process took the binding back");
removeAgentBindingFile({ pid, bridgeId: "retired", dir: tmp });
assert.equal(readAgentBindingFile({ pid, dir: tmp }).bridgeId, "successor", "the older process's exit removed its successor's binding");
// Control: in arrival order, the younger process replaces the older one's binding.
fs.rmSync(file, { force: true });
writeAgentBindingFile({ pid, agentId: "agent-a", bridgeId: "retired", startedAt: older, dir: tmp });
assert.equal(writeAgentBindingFile({ pid, agentId: "agent-a", bridgeId: "successor", startedAt: younger, dir: tmp }), true);
assert.equal(readAgentBindingFile({ pid, dir: tmp }).bridgeId, "successor");

fs.rmSync(tmp, { recursive: true, force: true });
console.log("binding-file.test.js: all assertions passed");
