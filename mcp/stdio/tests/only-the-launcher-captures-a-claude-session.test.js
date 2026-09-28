// The Claude session capture hook runs only in a claude the launcher started (review of 658e67df).
//
// `claude-session-hook.js` writes the session id keyed by AIFY_AGENT_ID, and the bridge's discovery reads
// it first. A `claude` run from an agent's own shell inherits AIFY_AGENT_ID, so IF it ran this hook it
// would overwrite its parent's capture, and the parent's own, registered bridge would then report the
// nested session (comms-senior-dev simulated exactly that). It cannot, because the hook is never in a
// settings file a bare `claude` loads: the launcher hands it over per launch, as `--settings`, and no
// installer writes it into ~/.claude/settings.json or a project's settings. This holds that true; the
// day an installer adds the hook to a shared settings file, the capture needs scoping to its own session.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { siblingCheckout } from "./_sibling-checkout.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const HOOK = "claude-session-hook.js";

test("no installer writes the capture hook into a settings file every claude loads", () => {
  const installer = fs.readFileSync(path.join(REPO, "install.sh"), "utf8");
  // CONTROL: the installer does write aify hooks into ~/.claude/settings.json, so a search there can find one.
  assert.match(installer, /install_claude_turn_start_hook/);
  assert.ok(!installer.includes(HOOK), "install.sh names the capture hook; a bare nested claude would run it");
});

test("the capture hook rides only in the launcher's per-launch --settings", () => {
  const wrapper = siblingCheckout("aify-wrapper", path.join("wrappers", "claude-aify.sh.in"));
  assert.ok(wrapper.dir, `no aify-wrapper checkout; looked in ${wrapper.looked.join(", ")} (set ${wrapper.variable})`);
  const template = fs.readFileSync(path.join(wrapper.dir, "wrappers", "claude-aify.sh.in"), "utf8");
  assert.ok(template.includes(HOOK), "control: the launcher is where the capture hook is installed");
  assert.match(template, /CLAUDE_MCP_FLAGS\+=\(--settings "\$AIFY_HOOK_SETTINGS"\)/,
               "the hook file is handed to that one claude as --settings, which no child inherits");
});
