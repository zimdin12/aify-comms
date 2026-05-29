// Regression: defaultMachineId() must return a lowercased "<platform>:<host>"
// id. Hostnames report with inconsistent casing across launch paths (e.g.
// win32:StevenZ-L vs win32:STEVENZ-L). The service compares machine_id
// case-insensitively for bridge supersession, so clients must send a
// consistent (lowercased) value to avoid duplicate live bridge_instances.
import assert from "assert";
import test from "node:test";
import { defaultMachineId } from "../runtimes.js";

test("defaultMachineId is fully lowercase", () => {
  const id = defaultMachineId();
  assert.strictEqual(id, id.toLowerCase(),
    `machine id must contain no uppercase chars — got ${id}`);
});

test("defaultMachineId lowercases a mixed-case hostname", () => {
  const prev = process.env.AIFY_MACHINE_ID;
  try {
    process.env.AIFY_MACHINE_ID = "StevenZ-L";
    const id = defaultMachineId();
    assert.strictEqual(id, id.toLowerCase(),
      `mixed-case hostname must be lowercased — got ${id}`);
    assert.ok(id.endsWith(":stevenz-l"),
      `expected host segment lowercased — got ${id}`);
  } finally {
    if (prev === undefined) delete process.env.AIFY_MACHINE_ID;
    else process.env.AIFY_MACHINE_ID = prev;
  }
});
