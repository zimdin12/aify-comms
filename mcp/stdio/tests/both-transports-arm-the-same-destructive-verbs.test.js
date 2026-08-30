#!/usr/bin/env node
// A destructive tool must be armed on BOTH transports, not whichever one someone edited last.
//
// THE DEFECT. `comms_clear` wipes every message, shared artifact and agent identity on the server,
// other teams included. On stdio it said so in 598 bytes. On SSE it said, in 73: "Clear messages,
// shared files, agents, or everything. Optional age filter." Same endpoint, same destruction, and
// which warning an agent received depended only on how it happened to be connected.
//
// `comms_channel_delete` had the SAME defect pointing the other way, which is what rules out
// "someone forgot once". SSE's docstring called it THE MOST DESTRUCTIVE DELETE AN AGENT CAN REACH;
// stdio carried those exact words as a CODE COMMENT directly beneath a 105-byte description, where
// no agent could ever read them. The warning existed and was invisible.
//
// WHY NOTHING CAUGHT IT. `test_both_transports_declare_the_same_tool_surface.py` compares tool NAMES
// and PARAMETER names. Descriptions were never compared at all, and 11 of 22 shared tools had
// drifted below 0.45 similarity by 2026-08-30. A name-set gate cannot see a warning going missing.
//
// WHAT THIS GATE DOES NOT DO. It does not require the two descriptions to match. They legitimately
// differ -- stdio has ambient identity and a local filesystem, SSE has neither. It requires only
// that a verb declared destructive CARRIES ITS WARNING on both sides, and that neither side is a
// stub of the other. Wording stays free; the arming does not.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";

import { registerAllTools } from "../register-tools.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const SSE_DIR = path.join(REPO, "service", "sse");

/**
 * The verbs that destroy data other agents depend on, and the concepts their warning must carry.
 *
 * Each entry lists alternatives: the description must contain at least one from EVERY group. That
 * keeps wording free while pinning the facts an agent needs to decide not to call the tool.
 */
const DESTRUCTIVE_VERBS = {
  comms_clear: [
    ["destructive"],
    ["irreversible", "no undo", "there is no undo"],
    ["whole hub", "other teams", "not just for you"],
  ],
  comms_channel_delete: [
    ["destructive"],
    ["every message", "its messages", "shared history"],
    ["leave"],
  ],
};

/** Real stdio descriptions, harvested by REGISTERING the tools against a stub server. */
function stdioDescriptions() {
  const found = new Map();
  const server = {
    tool: (name, description) =>
      found.set(name, typeof description === "string" ? description : ""),
  };
  registerAllTools(server, z, { ensureDispatchLoop: () => {} });
  return found;
}

/** SSE descriptions are the docstrings FastMCP publishes; read them from the source. */
function sseDescriptions() {
  const found = new Map();
  for (const file of fs.readdirSync(SSE_DIR).filter((f) => f.endsWith(".py"))) {
    const text = fs.readFileSync(path.join(SSE_DIR, file), "utf8");
    const re = /async def (comms_\w+)\s*\([\s\S]*?\)[^:]*:\s*"""([\s\S]*?)"""/g;
    let m;
    while ((m = re.exec(text)) !== null) found.set(m[1], m[2]);
  }
  return found;
}

const carries = (text, groups) =>
  groups.filter((alts) => !alts.some((a) => text.toLowerCase().includes(a.toLowerCase())));

test("both readers find the verbs they are supposed to judge", () => {
  // POSITIVE CONTROL for every assertion below: a reader that silently found nothing would make
  // each `for` loop vacuous and this whole file green while proving nothing at all.
  const stdio = stdioDescriptions();
  const sse = sseDescriptions();
  assert.ok(stdio.size >= 30, `stdio harvested only ${stdio.size} tools`);
  assert.ok(sse.size >= 20, `sse harvested only ${sse.size} tools`);
  for (const name of Object.keys(DESTRUCTIVE_VERBS)) {
    assert.ok(stdio.has(name), `stdio is missing ${name}`);
    assert.ok(sse.has(name), `sse is missing ${name}`);
  }
});

test("every destructive verb carries its warning on the stdio transport", () => {
  const stdio = stdioDescriptions();
  for (const [name, groups] of Object.entries(DESTRUCTIVE_VERBS)) {
    const missing = carries(stdio.get(name) || "", groups);
    assert.equal(
      missing.length, 0,
      `stdio ${name} is missing: ${missing.map((g) => g.join("/")).join(", ")}`,
    );
  }
});

test("every destructive verb carries its warning on the SSE transport", () => {
  const sse = sseDescriptions();
  for (const [name, groups] of Object.entries(DESTRUCTIVE_VERBS)) {
    const missing = carries(sse.get(name) || "", groups);
    assert.equal(
      missing.length, 0,
      `sse ${name} is missing: ${missing.map((g) => g.join("/")).join(", ")}`,
    );
  }
});

test("neither transport is a stub of the other", () => {
  // The 598-vs-73 shape, caught without pinning any wording. A description under 40% the length of
  // its counterpart is not a different phrasing of the same warning; it is a different warning.
  const stdio = stdioDescriptions();
  const sse = sseDescriptions();
  for (const name of Object.keys(DESTRUCTIVE_VERBS)) {
    const a = (stdio.get(name) || "").length;
    const b = (sse.get(name) || "").length;
    const ratio = Math.min(a, b) / Math.max(a, b);
    assert.ok(
      ratio >= 0.4,
      `${name}: stdio ${a}B vs sse ${b}B (ratio ${ratio.toFixed(2)}) -- one side is a stub`,
    );
  }
});

test("the gate detects a planted violation", () => {
  // NEGATIVE CONTROL. Everything above is an absence check, and an absence check that cannot fail
  // is decoration. This proves the matcher says no when the warning really is gone.
  const stripped = "Clear messages, shared files, agents, or everything. Optional age filter.";
  const missing = carries(stripped, DESTRUCTIVE_VERBS.comms_clear);
  assert.equal(missing.length, 3, "the exact pre-fix SSE description must fail all three groups");
});
