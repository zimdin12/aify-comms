// Which runtime this bridge is driving, and what happens when it cannot tell.
//
// `AIFY_RUNTIME` names the coding agent this process serves; `adapterFor` turns that name into the adapter
// that reads its transcript, finds its session id, and describes its environment. Sixteen places across six
// unrelated concerns read the result and none of the resolution was reachable from a test: it lived in
// `server.js`, the bin entry point, and nothing imports that.
//
// THE PROPERTY WORTH THE FILE IS THAT AN UNKNOWN RUNTIME IS NOT AN ERROR. `adapterFor` throws on a name it
// does not recognise, and a bridge that refused to start on an unfamiliar `AIFY_RUNTIME` would take an agent
// down over a value most of its work does not need. So the resolution swallows the throw and leaves `null`,
// and all sixteen readers are written to expect it. `null` means "no transcript-level integration", never
// "broken" — and a future edit that let the throw escape would turn an unrecognised env value into a bridge
// that will not boot.
//
// Resolved at module load, so every case needs its own process.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { declaringModules } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "runtime-adapter.mjs")).href;

// Reports the adapter's identity rather than the object, so the assertions do not depend on an adapter's
// internal shape — only on WHICH adapter resolved, which is the module's whole contract.
function resolveAdapter(runtime) {
  const out = execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const m = await import(" + JSON.stringify(LEAF) + ");"
      + " const a = m.__runtimeAdapter;"
      + " process.stdout.write(JSON.stringify({"
      + "   isNull: a === null,"
      + "   name: a && a.name ? a.name : null,"
      + "   hasTranscriptTail: !!(a && typeof a.transcriptTail === 'function'),"
      + " }));"],
    { env: { ...process.env, AIFY_RUNTIME: runtime }, encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] },
  );
  return JSON.parse(out);
}

test("a known runtime resolves to its adapter", () => {
  // `claude-code` and `codex` are the two the detectors branch on by name, so those names are contractual
  // rather than incidental — `agent-summary.mjs` and both turn detectors compare against them.
  const claude = resolveAdapter("claude-code");
  assert.equal(claude.isNull, false, "claude-code must resolve an adapter");
  assert.equal(claude.name, "claude-code", "and the adapter must report that name");

  const codex = resolveAdapter("codex");
  assert.equal(codex.isNull, false, "codex must resolve an adapter");
  assert.equal(codex.name, "codex");
});

test("the lookup is CASE-INSENSITIVE and ALIAS-AWARE, which I had assumed it was not", () => {
  // Read from `adapterFor` after my "unknown runtime" list failed on two entries I had put in it. It
  // lowercases the name and maps aliases, so `CLAUDE` and `claude` both reach `claude-code`. The code is
  // more forgiving than I assumed — the seventh time in this decomposition that my assumption about a callee
  // was more pessimistic than the implementation.
  //
  // Worth a positive test rather than a corrected negative one: wrappers and configs write this value by
  // hand, and case-folding plus aliases is what stops `AIFY_RUNTIME=Claude` from silently disabling every
  // transcript-level feature.
  for (const spelling of ["claude-code", "claude", "CLAUDE", "Claude_Code"]) {
    const r = resolveAdapter(spelling);
    assert.equal(r.isNull, false, `${spelling} must resolve`);
    assert.equal(r.name, "claude-code", `${spelling} must reach the canonical adapter`);
  }
  assert.equal(resolveAdapter("CODEX").name, "codex", "case folding applies to every runtime, not just claude");
});

test("AN UNKNOWN RUNTIME LEAVES null AND DOES NOT THROW — the property this file exists for", () => {
  // `adapterFor` throws on an unrecognised name. If that escaped, an unfamiliar AIFY_RUNTIME would be a
  // bridge that will not boot, for a value most of the bridge does not need.
  //
  // `CLAUDE` and `claude` were in this list until the run corrected me; they are aliases, not unknowns.
  // Every name below is genuinely absent from the registry and its alias table.
  for (const runtime of ["not-a-runtime", "gpt", "!!!", "codex-v2", "claude-code-v2", "opencode-x"]) {
    let result;
    assert.doesNotThrow(
      () => { result = resolveAdapter(runtime); },
      `AIFY_RUNTIME=${runtime} must not prevent the module from loading`,
    );
    assert.equal(result.isNull, true, `${runtime} must resolve to null, not to a guessed adapter`);
  }
});

test("an unset or blank runtime is null too, without consulting the adapter registry", () => {
  // The common case: most bridges have no AIFY_RUNTIME at all. Whitespace must behave the same, because a
  // wrapper writing an empty value is indistinguishable from not writing one.
  for (const runtime of ["", "   ", "\t"]) {
    assert.equal(resolveAdapter(runtime).isNull, true, `${JSON.stringify(runtime)} must resolve to null`);
  }
});

test("the runtime name is trimmed before lookup", () => {
  // Wrappers and MCP configs interpolate this value, and a trailing newline is the classic result. Without
  // the trim, a legitimate runtime would silently become an unknown one and every transcript-level feature
  // would go quiet with no error to explain it.
  const padded = resolveAdapter("  codex  ");
  assert.equal(padded.isNull, false, "a padded runtime name must still resolve");
  assert.equal(padded.name, "codex");
});

test("the resolved adapter carries the capability the detectors gate on", () => {
  // Both turn detectors check `typeof adapter.transcriptTail === "function"` before arming. If a resolved
  // adapter lacked it they would silently not arm, which reads as an agent that simply never reports turns.
  assert.equal(resolveAdapter("claude-code").hasTranscriptTail, true);
  assert.equal(resolveAdapter("codex").hasTranscriptTail, true);
});

test("exactly one module declares it, and the bridge still reads it", () => {
  assert.deepEqual(
    declaringModules("__runtimeAdapter"), [{ file: "runtime-adapter.mjs", kind: "binding" }],
    "__runtimeAdapter must be declared exactly once, by its owner",
  );
});

test("the owner reaches only the adapter registry, and holds nothing else", () => {
  const src = readFileSync(path.join(STDIO, "runtime-adapter.mjs"), "utf-8");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ["./adapters/index.js"], "one import: the adapter registry");
  // One binding only. A second name here would make this the "runtime stuff" module rather than an owner.
  assert.equal((src.match(/^export /gm) || []).length, 1, "exactly one export");
});
