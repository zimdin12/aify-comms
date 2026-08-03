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

fs.rmSync(tmp, { recursive: true, force: true });
console.log("binding-file.test.js: all assertions passed");
