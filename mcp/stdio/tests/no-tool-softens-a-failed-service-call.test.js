// No bridge tool turns a failed service call into an empty answer.
//
// THE SIBLING OF A REAL DEFECT. The SSE transport's `comms_*` tools read `_api`'s result without
// checking, so a 500 rendered as "No agents registered.", "No channels.", "Run not found: X" and
// 'No results ... (searched: nothing)' -- to an AGENT, which then acts on it. Four tools, measured
// 2026-08-28 by handing each a canned HTTP 500. `inbox_tools.py` itself warned that the two
// transports drift: "Fixing one transport and not the other would have left half the fleet misled."
//
// THE BRIDGE DOES NOT HAVE THAT DEFECT, and the reason is structural rather than careful. Its
// `makeAifyHttpCall` THROWS on a non-OK status instead of returning a body, so a caller cannot read
// an error as data. Its two soft returns -- `null` when no base URL is configured, `{}` when a 200
// will not parse -- are then dereferenced directly (`r.messages.length`), which throws a TypeError.
// A throw is fail-closed: the transport hands the agent an error.
//
// WHAT THIS PINS is that the property stays structural. Measured: 16 tool modules, 34 bindings of an
// httpCall result, ZERO softened. One `?? {}` or `|| []` on one of them would reintroduce exactly the
// SSE defect on this side, and nothing else in the suite would notice -- the tool would simply start
// answering an outage with an empty list.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const BRIDGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Every tool module, DISCOVERED. A hand-listed set is one a new tool file escapes. */
function toolSources() {
  return fs.readdirSync(BRIDGE)
    .filter((name) => /-tools?\.mjs$/.test(name) && !name.includes(".test."))
    .map((name) => [name, fs.readFileSync(path.join(BRIDGE, name), "utf8")]);
}

/** Lines binding the result of a service call. */
const BINDING = /\bconst\s+\w+\s*=\s*await\s+(?:httpCall|aifyCall)\s*\(/;

/** The same line ALSO softening that result into a default. */
const SOFTENED = /await\s+(?:httpCall|aifyCall)\s*\([^;]*\)\s*\)?\s*(?:\?\?|\|\|)|\)\s*\)?\s*\?\./;

test("the scan finds the tool modules and their service calls", () => {
  // Controls. An empty file list or a binding pattern that matches nothing makes the assertion below
  // pass while reading nothing at all -- the wrong zero this repo keeps producing.
  const sources = toolSources();
  assert.ok(sources.length >= 10, `only ${sources.length} tool modules found`);
  const bindings = sources.reduce(
    (n, [, text]) => n + text.split("\n").filter((line) => BINDING.test(line)).length,
    0,
  );
  assert.ok(bindings >= 20, `only ${bindings} service-call bindings found; the scan has drifted`);
});

test("the scan can recognise a softened call", () => {
  // The negative control. A pattern that never matches would make this gate decorative, so it is
  // checked against the exact shapes that would reintroduce the SSE defect.
  assert.ok(SOFTENED.test('const r = await httpCall("GET", "/agents") ?? {};'));
  assert.ok(SOFTENED.test('const r = (await httpCall("GET", "/agents")) || {};'));
  assert.ok(!SOFTENED.test('const r = await httpCall("GET", "/agents");'));
});

test("no tool softens a failed service call into an empty answer", () => {
  const offenders = [];
  for (const [name, text] of toolSources()) {
    text.split("\n").forEach((line, index) => {
      if (!BINDING.test(line)) return;
      if (SOFTENED.test(line)) offenders.push(`${name}:${index + 1}  ${line.trim().slice(0, 80)}`);
    });
  }
  assert.deepStrictEqual(
    offenders, [],
    "a tool defaults a failed service call to an empty value, so an outage will read to the agent "
      + "as a fact about the fleet -- the defect the SSE transport had: " + offenders.join("; "),
  );
});

console.log("no-tool-softens-a-failed-service-call.test.js: all assertions passed");
