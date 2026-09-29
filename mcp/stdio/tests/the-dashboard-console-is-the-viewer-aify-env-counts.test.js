#!/usr/bin/env node
// The names the dashboard console types and resizes under are the ones aify-env counts as a viewer,
// and the name everything else arrives under is not.
//
// THE SEAM. aify-env gives a terminal back to the size of the viewer that types into it, and decides
// who that viewer is from the control's `requestedBy` (`lib/plugins/aify-comms/control-viewer.mjs`).
// The dashboard chooses those names (`CONSOLE_VIEWER` in `terminal-input.mjs`, and the attach and
// Refresh resizes), and the service fills in `dashboard` for any caller that named nobody
// (`recorded_operator_actor`, service/api_core/operator_authz.py). A rename on either side breaks
// size ownership with every suite green: named wrong, the console's keys stop restoring its size; the default counted as a viewer, a
// chat message or a Compact resizes the operator's Herdr pane (external review of 0.7.6, ST1).
//
// Both modules are imported, not read: each is pure and has no imports of its own. The dashboard's
// terminal requests are found by reading its source, with a floor on how many, so an extractor that
// stopped matching cannot pass by finding none.
//
// IT FAILS RATHER THAN SKIPS without the aify-env checkout, like every cross-repo proof here.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { siblingCheckout } from "./_sibling-checkout.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const DASHBOARD = path.join(REPO, "service", "new_dashboard");

async function hostViewerRule() {
  const { dir, looked } = siblingCheckout("aify-env", path.join("lib", "plugins", "aify-comms", "control-viewer.mjs"));
  assert.ok(dir, `the aify-env checkout was not found, so this seam was NOT verified. Looked in: ${looked.join(", ")}`);
  return import(pathToFileURL(path.join(dir, "lib", "plugins", "aify-comms", "control-viewer.mjs")).href);
}

/** Every `requestedBy` the dashboard puts on a terminal input or resize request. */
function dashboardTerminalRequesters() {
  const found = [];
  for (const name of readdirSync(DASHBOARD).filter((f) => f.endsWith(".mjs") && !f.endsWith(".test.mjs"))) {
    const source = readFileSync(path.join(DASHBOARD, name), "utf8");
    for (const call of source.matchAll(/\/terminals\/\$\{[^}]*\}\/(input|resize)`,[\s\S]{0,200}?requestedBy: ([A-Za-z_]+|'[^']*')/g)) {
      found.push({ file: name, action: call[1], requestedBy: call[2] });
    }
  }
  return found;
}

test("every name the dashboard console types or resizes under is the viewer aify-env counts", async () => {
  const { viewerOfControl, DASHBOARD_VIEWER } = await hostViewerRule();
  const { CONSOLE_VIEWER } = await import(pathToFileURL(path.join(DASHBOARD, "terminal-input.mjs")).href);
  const found = dashboardTerminalRequesters();
  // Four today: the keystroke poster, the console's resize, the attach nudge, the Refresh repaint.
  assert.ok(found.length >= 4, `CONTROL: the extractor found ${found.length} terminal requests: ${JSON.stringify(found)}`);
  for (const { file, action, requestedBy } of found) {
    const value = requestedBy === "CONSOLE_VIEWER" ? CONSOLE_VIEWER : requestedBy.replace(/^'|'$/g, "");
    assert.equal(viewerOfControl({ requestedBy: value }), DASHBOARD_VIEWER,
      `${file}: a terminal ${action} sent as '${value}' is not the dashboard viewer on the host`);
  }
});

test("no agent id can be the dashboard viewer: the surface namespace is outside every agent id", async () => {
  // An agent's console input is recorded under its id. The surfaces were `dashboard-*` first, the shape
  // of a live agent's id (`dashboard-manager`), whose typing would then have resized the operator's pane.
  const { viewerOfControl, DASHBOARD_SURFACE_PREFIX } = await hostViewerRule();
  const source = readFileSync(path.join(REPO, "service", "api_core", "validation.py"), "utf8");
  const pattern = /^SAFE_NAME_RE = re\.compile\(r'(.+)'\)$/m.exec(source)?.[1];
  assert.ok(pattern, "CONTROL: validation.py no longer declares SAFE_NAME_RE as a raw-string literal");
  const agentId = new RegExp(pattern.replace(/\\Z$/, "$"));
  assert.ok(agentId.test("dashboard-manager"), "CONTROL: the agent-id rule admits a real agent id");
  assert.ok(DASHBOARD_SURFACE_PREFIX && !agentId.test(`${DASHBOARD_SURFACE_PREFIX}x`),
    `an agent could be named '${DASHBOARD_SURFACE_PREFIX}x' and act as the dashboard viewer`);
  assert.equal(viewerOfControl({ requestedBy: "dashboard-manager" }), "");
});

test("the requester the service fills in for a caller that named nobody is no viewer", async () => {
  const { viewerOfControl } = await hostViewerRule();
  // The terminal routes record an unnamed caller through `recorded_operator_actor`, whose fallback is
  // `DASHBOARD_ACTOR` (service/api_core/operator_authz.py).
  const routes = readFileSync(path.join(REPO, "service", "routers", "terminals.py"), "utf8");
  const recorded = routes.match(/recorded_operator_actor\(req\.requestedBy,/g) ?? [];
  assert.ok(recorded.length >= 2, "CONTROL: terminal input and resize no longer record their requester through "
    + "recorded_operator_actor; repoint this test at whatever fills in an unnamed caller now");
  const authz = readFileSync(path.join(REPO, "service", "api_core", "operator_authz.py"), "utf8");
  const fallback = /^DASHBOARD_ACTOR = "([^"]+)"$/m.exec(authz)?.[1];
  assert.ok(fallback, "CONTROL: operator_authz.py no longer declares DASHBOARD_ACTOR as a string literal");
  assert.equal(viewerOfControl({ requestedBy: fallback }), "",
    `an input with no named requester arrives as '${fallback}' and would resize the terminal`);
});
