// The `agent-state-publishers` doctor row: ok only when some aify-env publisher reports fresh and none says it cannot
// read its own state. A stale old key is named but changes nothing, since it yields to a live peer (task #5).
import assert from "node:assert/strict";
import test from "node:test";

import { checkAgentStatePublishers, publishersVerdict } from "../agent-state-publishers-check.mjs";

const publisher = (instance, availability, agents = 3) => ({ machineId: "win32:host", instance, availability, agents });

test("a fresh publisher is ok, and a stale old key beside it is named without failing", () => {
  const v = publishersVerdict({ readable: true, publishers: [publisher("herdr-live", "fresh"), publisher("herdr-dead", "stale", 0)] });
  assert.equal(v.ok, true);
  assert.equal(v.code, "fresh");
  assert.match(v.detail, /1 of 2 publisher\(s\) fresh; not reporting fresh: win32:host\/herdr-dead \(stale, 0 agents\)/);
});

test("no fresh publisher fails: every agent would read unknown", () => {
  const v = publishersVerdict({ readable: true, publishers: [publisher("herdr-dead", "stale")] });
  assert.deepEqual([v.ok, v.code], [false, "no-fresh-publisher"]);
  assert.deepEqual([publishersVerdict({ readable: true, publishers: [] }).ok], [false], "none at all is not fresh either");
});

test("a publisher that cannot read its own state fails even beside a fresh one", () => {
  for (const availability of ["unavailable", "lost"]) {
    const v = publishersVerdict({ readable: true, publishers: [publisher("a", "fresh"), publisher("b", availability)] });
    assert.deepEqual([v.ok, v.code], [false, "publisher-unavailable"], availability);
    const says = availability === "lost" ? "lost by this service until their next snapshot" : "cannot read their own state";
    assert.match(v.detail, new RegExp(`${says}: win32:host/b \\(${availability}`));
  }
});

test("a lost mirror, and no answer at all, are failures that say which", () => {
  assert.deepEqual([publishersVerdict({ readable: false, publishers: [] }).code], ["lost"]);
  for (const answer of [null, {}, { publishers: "x" }]) assert.equal(publishersVerdict(answer).code, "unknown");
});

test("the row asks the service's own route and reports through add, with a fix only when it fails", async () => {
  const rows = [];
  const add = (...row) => rows.push(row);
  await checkAgentStatePublishers({ get: async (path) => (path === "/api/v1/agent-state/publishers"
    ? { readable: true, publishers: [publisher("live", "fresh")] } : null), add });
  await checkAgentStatePublishers({ get: async () => { throw new Error("down"); }, add });
  assert.deepEqual(rows[0].slice(0, 3), ["agent-state-publishers", true, "fresh"]);
  assert.equal(rows[0][4], undefined);
  assert.deepEqual(rows[1].slice(0, 3), ["agent-state-publishers", false, "unknown"]);
  assert.match(rows[1][4], /aify-env doctor/);
});
