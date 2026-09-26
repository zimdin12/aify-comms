// Every registration and every liveness beat a bridge sends carries its build.
//
// `aify-comms doctor`'s `bridge-current` row judges running code by the `bridgeBuild` each live bridge
// reported. Its tests feed the verdict synthetic builds, so a sender that stopped sending one would
// read as a bridge too old to report, and nothing would say which sender went quiet (v0.7.1 review,
// T01). The senders take two shapes, both derived here from the source rather than listed: a
// registration payload carries `bridgeStartedAt` (the tombstone guard every registration sends), and a
// liveness beat carries `liveness: true`. The object literal around each must also say `bridgeBuild`.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SENDER = /\bbridgeStartedAt:\s*BRIDGE_STARTED_AT\b|\bliveness:\s*true\b/g;

/** The text of the object literal enclosing `index`, found by matching its braces. */
function enclosingObject(source, index) {
  let depth = 0;
  let open = -1;
  for (let at = index; at >= 0; at -= 1) {
    if (source[at] === "}") depth += 1;
    else if (source[at] === "{" && depth-- === 0) { open = at; break; }
  }
  if (open < 0) return "";
  depth = 0;
  for (let at = open; at < source.length; at += 1) {
    if (source[at] === "{") depth += 1;
    else if (source[at] === "}" && (depth -= 1) === 0) return source.slice(open, at + 1);
  }
  return "";
}

function senders(source) {
  const found = [];
  for (const match of source.matchAll(SENDER)) {
    const line = source.slice(0, match.index).split("\n").length;
    const lineText = source.split("\n")[line - 1];
    if (/^\s*(\/\/|\*)/.test(lineText)) continue;
    found.push({ line, object: enclosingObject(source, match.index) });
  }
  return found;
}

const withoutBuild = (site) => !/\bbridgeBuild\s*:/.test(site.object);

test("every registration payload and liveness beat in the bridge carries bridgeBuild", () => {
  const offenders = [];
  let seen = 0;
  for (const file of readdirSync(STDIO).filter((f) => /\.m?js$/.test(f))) {
    for (const site of senders(readFileSync(path.join(STDIO, file), "utf8"))) {
      seen += 1;
      if (withoutBuild(site)) offenders.push(`${file}:${site.line}`);
    }
  }
  // POSITIVE CONTROL: the six known senders (server.js's beat, two registrations in
  // auto-registration.mjs, registration-tool.mjs, and the two channel-sidecar beats).
  assert.ok(seen >= 6, `the scan found only ${seen} senders`);
  assert.deepEqual(offenders, [], "these senders do not report the build their bridge is running");
});

test("NEGATIVE CONTROL: a beat without a build is flagged, one with it is not", () => {
  const bad = 'await httpCall("POST", `/agents/${id}/heartbeat`, {\n  bridgeId: x,\n  liveness: true,\n});\n';
  const good = 'await httpCall("POST", `/agents/${id}/heartbeat`, {\n  liveness: true,\n  bridgeBuild: BRIDGE_BUILD_TAG,\n});\n';
  assert.equal(senders(bad).filter(withoutBuild).length, 1);
  assert.equal(senders(good).filter(withoutBuild).length, 0);
});
