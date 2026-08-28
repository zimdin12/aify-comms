// The `env-processes` CHECK, not just the predicate it calls.
//
// `env-process-reconciliation.mjs` was written, mutation-tested and green a commit before anything
// called it. A proven helper with no call site is a failure this repo has paid for twice: the
// interrupt feature whose six tests all exercised the pure builder while the query it depended on
// read a table nothing wrote, and `doctor.js`'s service check, whose verdict everybody tested and
// whose early return bypassed it entirely.
//
// So the check lives in its own module and every collaborator is a parameter. Importing `doctor.js`
// RUNS the doctor and exits, which is why the logic could not stay there.
import assert from "node:assert/strict";
import { test } from "node:test";

import { checkEnvProcesses } from "../env-processes-check.mjs";

// QUOTED, because `launcherDelegation` matches `^export AIFY_COMMS_DELEGATE_SPAWNS="([^"]*)"` --
// the shape install.sh renders. An unquoted value in a fixture parses as NOT delegating, and the
// check then SKIPS: nine of these tests read `s.added[0]` as undefined on the first run, which is
// the fixture being wrong rather than the code.
const DELEGATING = [
  "#!/usr/bin/env bash",
  '# HARNESS_WRAPPER_VERSION=0.6.0',
  'export AIFY_COMMS_DELEGATE_SPAWNS="1"',
  'export AIFY_ENV_ENDPOINT="http://127.0.0.1:8802"',
  'exec node "$HOME/.aify-comms/mcp/stdio/server.js" --environment-bridge',
].join("\n");

const LOCAL = [
  "#!/usr/bin/env bash",
  '# HARNESS_WRAPPER_VERSION=0.6.0',
  'exec node "$HOME/.aify-comms/mcp/stdio/server.js" --environment-bridge',
].join("\n");

/** A recorder for the two sinks doctor.js supplies. */
function sink() {
  const added = [];
  const skipped = [];
  return {
    added,
    skipped,
    add: (id, ok, code, detail, fix) => { added.push({ id, ok, code, detail, fix }); },
    skip: (id, detail) => { skipped.push({ id, detail }); },
  };
}

const ENVIRONMENTS = {
  environments: [
    { id: "windows:host:default", machineId: "win32:host", status: "online" },
    { id: "wsl:other:default", machineId: "linux:other", status: "online" },
  ],
};

function serviceWith(terminals, { truncated = false } = {}) {
  return async (path) => {
    if (path.startsWith("/api/v1/terminals")) return { ok: true, terminals, truncated };
    if (path.startsWith("/api/v1/environments")) return ENVIRONMENTS;
    return null;
  };
}

test("with spawns NOT delegated the question is skipped, not passed", () => {
  // Answering `ok` would add a green row for work nobody did. There is no second list to compare
  // against when the bridge hosts its own terminals.
  const s = sink();
  return checkEnvProcesses({
    get: async () => null, add: s.add, skip: s.skip,
    fetchJson: async () => { throw new Error("must not be called"); },
    launcherText: LOCAL, machineId: "win32:host",
  }).then(() => {
    assert.equal(s.added.length, 0);
    assert.deepEqual(s.skipped.map((x) => x.id), ["env-processes"]);
  });
});

test("with no launcher at all the question is skipped", async () => {
  const s = sink();
  await checkEnvProcesses({
    get: async () => null, add: s.add, skip: s.skip,
    fetchJson: async () => null, launcherText: null, machineId: "win32:host",
  });
  assert.deepEqual(s.skipped.map((x) => x.id), ["env-processes"]);
});

test("aify-env not answering is UNKNOWN, not ok and not a pile of orphans", async () => {
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([]), add: s.add, skip: s.skip,
    fetchJson: async () => null, launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.equal(s.added[0].ok, false);
  assert.equal(s.added[0].code, "unknown");
});

test("the SERVICE not answering is UNKNOWN too, and says which row explains it", async () => {
  // Not a failure of this check. Reporting orphans because the terminal list could not be read would
  // be inventing findings out of an unrelated outage.
  const s = sink();
  await checkEnvProcesses({
    get: async () => null, add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [{ id: "p1", pid: 1, service: "aify-comms" }] }),
    launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.equal(s.added[0].code, "unknown");
  assert.match(s.added[0].fix, /service/i);
});

test("THE OPERATOR'S CASE reaches the report, end to end", async () => {
  // aify-env running a PTY whose terminal row is stopped. This is the whole reason the check exists,
  // driven through the real call rather than the predicate.
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([]),   // no LIVE terminals: the row for this pid is stopped
    add: s.add,
    skip: s.skip,
    fetchJson: async (url) => {
      assert.match(url, /\/processes$/, "the check asked aify-env for something other than its processes");
      return { processes: [{ id: "p1", pid: 155844, service: "aify-comms", label: "ef-manager" }] };
    },
    launcherText: DELEGATING,
    machineId: "win32:host",
  });
  assert.equal(s.added[0].ok, false);
  assert.equal(s.added[0].code, "unaccounted");
  assert.match(s.added[0].detail, /155844/);
  assert.match(s.added[0].detail, /ef-manager/);
});

test("it asks for LIVE terminals explicitly", async () => {
  // The default is live, and it is passed anyway because this check's meaning depends on it: a
  // listing including stopped rows would account for processes whose terminals ended -- the
  // operator's exact case, reported as healthy.
  let asked = "";
  const s = sink();
  await checkEnvProcesses({
    get: async (path) => {
      if (path.startsWith("/api/v1/terminals")) { asked = path; return { terminals: [] }; }
      return ENVIRONMENTS;
    },
    add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [] }),
    launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.match(asked, /status=live/, "the check would count a stopped terminal as accounting for a process");
});

test("a truncated listing is UNKNOWN rather than a report of orphans", async () => {
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([], { truncated: true }), add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [{ id: "p1", pid: 1, service: "aify-comms" }] }),
    launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.equal(s.added[0].code, "unknown");
  assert.match(s.added[0].detail, /truncated/);
});

test("phantoms are scoped to THIS host's environment", async () => {
  // A live terminal on another machine is not missing here. Without the scoping every other host's
  // terminals would be reported, which is an alarm that fires on a healthy fleet.
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([
      { id: "t-other", agentId: "elsewhere", status: "attached", processId: "777",
        environmentId: "wsl:other:default" },
    ]),
    add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [] }),
    launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.equal(s.added[0].ok, true, `another host's terminal was reported: ${s.added[0].detail}`);
});

test("a phantom on OUR environment is reported", async () => {
  // The control for the test above: the scoping must not be so tight that nothing is ever reported.
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([
      { id: "t1", agentId: "sc-coder", status: "attached", processId: "777",
        environmentId: "windows:host:default" },
    ]),
    add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [] }),
    launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.equal(s.added[0].ok, false);
  assert.equal(s.added[0].code, "phantom");
  assert.match(s.added[0].detail, /sc-coder/);
});

test("a clean host passes", async () => {
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([
      { id: "t1", agentId: "a", status: "attached", processId: "155844",
        environmentId: "windows:host:default" },
    ]),
    add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [{ id: "p1", pid: 155844, service: "aify-comms" }] }),
    launcherText: DELEGATING, machineId: "win32:host",
  });
  assert.equal(s.added[0].ok, true, s.added[0].detail);
  assert.equal(s.added[0].code, "ok");
});

test("an unidentifiable environment loses the phantom direction rather than inventing it", async () => {
  // Losing one direction is the safe way to be unsure. Reporting every live terminal as missing
  // because we could not tell which environment is ours would be the opposite.
  const s = sink();
  await checkEnvProcesses({
    get: serviceWith([
      { id: "t1", agentId: "a", status: "attached", processId: "777",
        environmentId: "windows:host:default" },
    ]),
    add: s.add, skip: s.skip,
    fetchJson: async () => ({ processes: [] }),
    launcherText: DELEGATING,
    machineId: "win32:a-machine-with-no-environment",
  });
  assert.equal(s.added[0].ok, true, `a phantom was reported for an unknown environment: ${s.added[0].detail}`);
});
