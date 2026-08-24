#!/usr/bin/env node
// Where managed spawns run, and whether that place is answering.
//
// Turning delegation on makes aify-env REQUIRED for spawning. `startDelegated` refuses rather than
// falling back, which is correct -- a silent fallback would put two spawners on one host, the
// collision the environment tier exists to end -- and it is invisible: what an operator sees is
// spawns failing, not a daemon that is down. This check names the cause before it is needed.
//
// It reads the installed launcher rather than running it. A bare `aify-comms` starts an environment
// bridge and supersedes the live one; this fleet lost nine managed agents to a four-second run of it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { spawnDelegationVerdict } from "../doctor-predicates.js";

const LOCAL = [
  '#!/usr/bin/env bash',
  'export AIFY_COMMS_DELEGATE_SPAWNS=""',
  'export AIFY_ENV_ENDPOINT=""',
].join("\n");

const DELEGATED = [
  '#!/usr/bin/env bash',
  'export AIFY_COMMS_DELEGATE_SPAWNS="1"',
  'export AIFY_ENV_ENDPOINT="http://127.0.0.1:8802"',
].join("\n");

test("the default is local hosting, and says why aify-env owns nothing", () => {
  const v = spawnDelegationVerdict({ launcherText: LOCAL });
  assert.equal(v.ok, true);
  assert.equal(v.code, "local");
  // The empty process list in aify-env's view is a consequence of this setting, and an operator
  // looking at that empty list should find the reason here rather than assume something is broken.
  assert.match(v.detail, /process list is empty by design/);
});

test("delegated and answering is ok, and names where", () => {
  const v = spawnDelegationVerdict({ launcherText: DELEGATED, endpointAnswered: true });
  assert.equal(v.ok, true);
  assert.equal(v.code, "delegated");
  assert.match(v.detail, /127\.0\.0\.1:8802/);
});

test("delegated and NOT answering fails, and says every spawn will fail", () => {
  // The whole point. Without this the symptom is "spawning is broken" with no cause attached.
  const v = spawnDelegationVerdict({ launcherText: DELEGATED, endpointAnswered: false });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unreachable");
  assert.match(v.detail, /will FAIL/);
  assert.match(v.fix, /Start aify-env/);
  assert.match(v.fix, /reinstall without --delegate-spawns/, "the way back must be stated too");
});

test("delegated but never asked is unknown-all, never a pass", () => {
  // The repo's standing rule, learned twice from this very tool: a check that gathered no evidence
  // must not report ok.
  const v = spawnDelegationVerdict({ launcherText: DELEGATED, endpointAnswered: null });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-all");
  assert.match(v.detail, /Nothing was verified/);
});

test("an unreadable launcher is unknown-all, not a quiet pass", () => {
  const v = spawnDelegationVerdict({ launcherText: null });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-all");
  assert.match(v.fix, /install\.sh/);
});

test("a launcher predating the setting is the default, not a failure", () => {
  // Every host upgrading into this check has one. Failing on that would be alarm fatigue on day one,
  // and it would be wrong: those launchers host spawns themselves, which is still the default.
  const v = spawnDelegationVerdict({ launcherText: "#!/usr/bin/env bash\nexec node bridge.js\n" });
  assert.equal(v.ok, true);
  assert.equal(v.code, "pre-contract");
  assert.match(v.detail, /predates/);
});

test("a baked-on setting with no endpoint still reports, rather than rendering undefined", () => {
  const text = 'export AIFY_COMMS_DELEGATE_SPAWNS="1"\n';
  const v = spawnDelegationVerdict({ launcherText: text, endpointAnswered: false });
  assert.equal(v.code, "unreachable");
  assert.match(v.detail, /no endpoint baked/);
  assert.ok(!/undefined/.test(v.detail), v.detail);
});

test("whitespace is not a setting", () => {
  const text = 'export AIFY_COMMS_DELEGATE_SPAWNS="   "\n';
  assert.equal(spawnDelegationVerdict({ launcherText: text }).code, "local");
});
