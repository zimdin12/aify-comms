// A deprecated runtime's test files are left out by default and run on request -- and nothing else is.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { deprecatedRuntimeEnabled, deprecatedRuntimeOf, disabledBy } from "./deprecated-runtimes.mjs";

test("the marker names its runtime, after a shebang or in a python comment", () => {
  assert.equal(deprecatedRuntimeOf("#!/usr/bin/env node\n// deprecated-runtime: pi\nimport x;"), "pi");
  assert.equal(deprecatedRuntimeOf("# deprecated-runtime: pi\n\"\"\"doc\"\"\""), "pi");
});

test("CONTROL: a file that only MENTIONS the marker in prose is not disabled", () => {
  assert.equal(deprecatedRuntimeOf("// this file is not a deprecated-runtime: pi test\n"), "");
  assert.equal(deprecatedRuntimeOf("import x;\n"), "");
});

test("a declared runtime runs only when AIFY_TEST_DEPRECATED names it or says all", () => {
  assert.equal(deprecatedRuntimeEnabled("pi", ""), false);
  assert.equal(deprecatedRuntimeEnabled("pi", "hermes"), false);
  assert.equal(deprecatedRuntimeEnabled("pi", "hermes, pi"), true);
  assert.equal(deprecatedRuntimeEnabled("pi", "all"), true);
  assert.equal(deprecatedRuntimeEnabled("", ""), true, "an unmarked file must always run");
});

test("disabledBy reads the file the runner would run", () => {
  const dir = mkdtempSync(join(tmpdir(), "aify-deprecated-"));
  const marked = join(dir, "a.test.js");
  const plain = join(dir, "b.test.js");
  writeFileSync(marked, "// deprecated-runtime: pi\n");
  writeFileSync(plain, "// nothing\n");
  assert.equal(disabledBy(marked, ""), "pi");
  assert.equal(disabledBy(marked, "pi"), "");
  assert.equal(disabledBy(plain, ""), "");
});
