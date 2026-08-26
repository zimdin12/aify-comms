// A DEFAULT PARAMETER IS ONLY EVER EVALUATED BY THE CALLER THAT OMITS THE ARGUMENT -- and in this
// repo that caller is always production, never a test.
//
// THE DEFECT THIS EXISTS TO CATCH, which was live for the whole of v0.5.4:
//
//   import { spawnSync } from "child_process";
//   export function defaultListProcesses(spawnSync = nodeSpawnSync) { try { ... } catch { return []; } }
//
// `nodeSpawnSync` is defined nowhere. The v0.5.4 extraction `32ce11fa` rewrote the import from
// `spawnSync as nodeSpawnSync` while leaving the signature byte-identical, and the alias the default
// depended on stopped existing.
//
// EVERY INSTRUMENT SAID GREEN:
//   - `node --check` passes. The syntax is valid; the name resolves at CALL time.
//   - The unit suite passes. Every test INJECTS a fake spawn, so the default never evaluates.
//   - The missing-sibling-import gate passes. `moduleBindings` treats every identifier in a
//     parameter list as BOUND, so a default's VALUE is indistinguishable from a parameter's NAME.
//
// AND THE FAILURE WAS SILENT, not loud. A default parameter evaluates BEFORE the function body, so
// the ReferenceError escapes the function's own try/catch -- and `enumerateManagedSurvivors` calls it
// as `try { procs = listProcesses() || [] } catch { procs = [] }`. The survivor sweep therefore
// enumerated ZERO processes and reaped nothing, on every platform, while reporting success. Seven
// orphaned managed processes were found alive on the operator's host on 2026-08-26.
//
// So this file calls the production entry points THE WAY PRODUCTION CALLS THEM: with no argument.
import assert from "node:assert/strict";
import test from "node:test";

import { defaultListProcesses } from "../proc-probes.js";

test("listing processes with no injected spawn returns a list rather than throwing", () => {
  // The whole bug in one line: production omits the argument, so the default is evaluated.
  const rows = defaultListProcesses();
  assert.ok(Array.isArray(rows), `expected an array, got ${typeof rows}`);
});

test("and the list it returns describes real processes", () => {
  // ANTI-VACUITY. `[]` is what the broken version's CALLER produced after swallowing the throw, so a
  // test that accepts an empty array passes against the defect it exists to catch. This process is
  // running, so the enumeration must at minimum find it.
  const rows = defaultListProcesses();
  const self = rows.find((row) => row && row.pid === process.pid);
  assert.ok(self, `the enumeration did not include this process (pid ${process.pid}); it found ${rows.length} row(s)`);
  assert.equal(typeof self.commandLine, "string");
  assert.ok(self.commandLine.length > 0, "a row with no command line cannot identify an agent");
});

test("an injected spawn still wins over the default", () => {
  // The default must not have displaced the injection point the reapers' tests depend on.
  const fake = () => ({ stdout: "111\t222\tnode fake.js\n", status: 0 });
  assert.deepEqual(defaultListProcesses(fake), [{ pid: 111, ppid: 222, commandLine: "node fake.js" }]);
});
