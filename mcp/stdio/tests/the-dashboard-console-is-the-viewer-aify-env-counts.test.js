#!/usr/bin/env node
// The names the dashboard console types and resizes under are the ones aify-env counts as a viewer,
// and the name everything else arrives under is not.
//
// THE SEAM. aify-env gives a terminal back to the size of the viewer that types into it, and decides
// who that viewer is from the control's `requestedBy` (`lib/plugins/aify-comms/control-viewer.mjs`).
// The dashboard chooses those names (`CONSOLE_VIEWER` in `terminal-input.mjs`, and the attach and
// Refresh resizes), and the service fills in `dashboard` for any caller that named nobody
// (`service/routers/terminals.py`). A rename on either side breaks size ownership with every suite
// green: named wrong, the console's keys stop restoring its size; the default counted as a viewer, a
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

test("the requester the service fills in for a caller that named nobody is no viewer", async () => {
  const { viewerOfControl } = await hostViewerRule();
  const source = readFileSync(path.join(REPO, "service", "routers", "terminals.py"), "utf8");
  const defaults = [...source.matchAll(/str\(req\.requestedBy or "([^"]+)"\)/g)].map((m) => m[1]);
  assert.ok(defaults.length >= 2, "CONTROL: terminals.py no longer defaults requestedBy textually; repoint this test");
  for (const fallback of new Set(defaults)) {
    assert.equal(viewerOfControl({ requestedBy: fallback }), "",
      `an input with no named requester arrives as '${fallback}' and would resize the terminal`);
  }
});
