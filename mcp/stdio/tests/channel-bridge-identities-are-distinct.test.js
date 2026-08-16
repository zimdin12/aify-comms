#!/usr/bin/env node
// Three drivers compose a bridge id with IDENTICAL code and DIFFERENT prefixes. That is the design.
//
//   claude-channel.js      channel-${MACHINE_ID}                 (claude channel sidecar)
//   hermes-channel.js      hermes-channel-${MACHINE_ID}          (hermes channel sidecar)
//   hermes-run-reporting   hermes-managed-host-${MACHINE_ID}     (hermes managed host)
//
// `channelBridgeId(agentId)` is byte-identical in all three — `prefix ? `${PREFIX}-${id}` : PREFIX`
// — so a duplication scan flags it as a three-way fork. IT IS NOT ONE. The bodies are the same; the
// module-scope constant each body CLOSES OVER is not. Merging them into a shared helper, which is
// exactly the tidy-up a fork census invites, would give three processes that routinely run side by
// side the SAME bridge identity.
//
// WHAT A COLLISION COSTS. Bridge identity is what the service uses to decide that a newer
// registration SUPERSEDES an older one; a superseded bridge is shut down and its managed workers are
// reaped. This project has already lost a nine-agent fleet to one unintended supersession. Two
// drivers sharing an id means each new registration evicts the other, forever.
//
// So the invariant is not "these three agree" but its opposite: for any agent, the three identities
// must be PAIRWISE DISTINCT. Nothing asserted that — `channel-bridge-id.test.js` covers the claude
// copy alone (agent-scoping and stability), which is true of all three and says nothing about
// whether they collide.
//
// hermes-channel.js does not export its copy, so its half is read from source. Two are called.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { channelBridgeId as claudeChannelBridgeId } from "../claude-channel.js";
import {
  CHANNEL_BRIDGE_PREFIX as HERMES_HOST_PREFIX,
  channelBridgeId as hermesHostBridgeId,
} from "../hermes-run-reporting.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceOf = (rel) => readFileSync(path.join(STDIO, rel), "utf-8").replace(/\r\n/g, "\n");

/** The literal template each module builds its prefix from, e.g. "hermes-channel-${MACHINE_ID}". */
function prefixTemplate(rel) {
  const src = sourceOf(rel);
  const match = src.match(/CHANNEL_BRIDGE_PREFIX\s*=\s*`([^`]*)`/);
  assert.ok(match, `no CHANNEL_BRIDGE_PREFIX template in ${rel} — if it moved, repoint this test`);
  return match[1];
}

const TEMPLATES = {
  "claude-channel.js": prefixTemplate("claude-channel.js"),
  "hermes-channel.js": prefixTemplate("hermes-channel.js"),
  "hermes-run-reporting.mjs": prefixTemplate("hermes-run-reporting.mjs"),
};

// ── the three namespaces are distinct ────────────────────────────────────────────────────────
{
  const values = Object.values(TEMPLATES);
  assert.equal(
    new Set(values).size, values.length,
    "two channel drivers now build their bridge id from the SAME prefix. They run side by side, so "
      + "each registration will supersede the other and reap its managed workers. If a merge was "
      + `intended, it is not: ${JSON.stringify(TEMPLATES, null, 2)}`,
  );
  for (const [file, template] of Object.entries(TEMPLATES)) {
    assert.match(
      template, /\$\{MACHINE_ID\}/,
      `${file}: the prefix must include MACHINE_ID — two machines running the same driver would `
        + `otherwise share one bridge identity`,
    );
  }
}

// ── and the two callable ones do not collide for a real agent ────────────────────────────────
{
  for (const agentId of ["lc-coder", "sc-manager", "a"]) {
    assert.notEqual(
      claudeChannelBridgeId(agentId), hermesHostBridgeId(agentId),
      `the claude channel sidecar and the hermes managed host produced the same bridge id for `
        + `"${agentId}"`,
    );
  }
  // The empty-agent fallback is the bare prefix, and must still not collide.
  assert.notEqual(claudeChannelBridgeId(""), hermesHostBridgeId(""));
  assert.equal(hermesHostBridgeId(""), HERMES_HOST_PREFIX, "an id-less caller falls back to the prefix");
}

// ── each is still agent-scoped and stable (true of all three, and not what makes them safe) ──
{
  for (const make of [claudeChannelBridgeId, hermesHostBridgeId]) {
    assert.notEqual(make("lc-coder"), make("lc-tester"), "bridge ids must be per-agent");
    assert.equal(make("lc-coder"), make("lc-coder"), "and stable across polls");
    assert.equal(make("  lc-coder  "), make("lc-coder"), "padding must not fork an identity");
  }
}

// ── anti-vacuity ─────────────────────────────────────────────────────────────────────────────
{
  // The distinctness assertions would pass against templates that shared no structure at all, and
  // the scoping ones against any function that echoed its argument. Both must hold together: same
  // SHAPE, different NAMESPACE.
  assert.equal(new Set(Object.values(TEMPLATES)).size, 3);
  for (const template of Object.values(TEMPLATES)) assert.match(template, /-\$\{MACHINE_ID\}$/);
  assert.ok(claudeChannelBridgeId("x").endsWith("-x"), "the agent id is the suffix");
}

console.log("channel-bridge-identities-are-distinct.test.js: all assertions passed");
