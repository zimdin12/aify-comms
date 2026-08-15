// No NEW ESM import cycle, and the one that exists may not grow.
//
// The JS counterpart to service/tests/test_no_import_cycles.py, and the more dangerous side. Python
// raises ImportError on a cycle for at least some entry orderings. Node does not: ESM resolves a
// cycle by hoisting bindings, so nothing fails at load. The binding is simply in its temporal dead
// zone while the other module's body runs, and you get a ReferenceError at CALL time — or silently
// `undefined` if the value is only read later. A cycle here is invisible until production.
//
// THERE IS ONE CYCLE AND IT IS DELIBERATE. `adapters/index.js` documents it in full: the Plan 3
// controllers made runtimes.js -> adapters/index.js -> adapters/X.js -> controllers/X.js ->
// runtimes-helpers.js -> runtimes.js, and the registry Map is built LAZILY on first `adapterFor()`
// call precisely so no adapter class is read during the re-entrant load, when its binding is still
// in TDZ. That mitigation is sound and this gate does not ask for it to be undone.
//
// WHAT THE GATE IS FOR: the mitigation holds only while the cycle stays what it is. The membership is
// pinned MODULE BY MODULE rather than by count, because a count would let one module leave and
// another join with the total unchanged — and the new one would be a module nobody checked for a
// load-time read of a cyclic binding. Adding an eighteenth fails here, which is the actual risk.
//
// Static `import ... from` only. `import()` is dynamic and cannot close a load-time cycle, the same
// reason the Python gate excludes function-scope imports.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
assert.ok(fs.existsSync(path.join(REPO, ".git")), `repo root wrong: ${REPO}`);

const ROOTS = ["mcp/stdio", "service/new_dashboard"];
const SKIP_DIRS = new Set(["node_modules", ".git", "fixtures", "tests", "__pycache__"]);

// The one component that exists, pinned by MEMBERSHIP. See the header for why it is allowed and why
// it is pinned this way rather than by size.
const KNOWN_CYCLES = [
  [
    "mcp/stdio/adapters/codex.js",
    "mcp/stdio/adapters/hermes.js",
    "mcp/stdio/adapters/index.js",
    "mcp/stdio/adapters/pi.js",
    "mcp/stdio/codex-session.js",
    "mcp/stdio/controllers/codex-controller.js",
    "mcp/stdio/controllers/codex-managed-controller.js",
    "mcp/stdio/controllers/hermes-controller.js",
    "mcp/stdio/controllers/hermes-managed-controller.js",
    "mcp/stdio/controllers/pi-controller.js",
    "mcp/stdio/hermes-managed-gateway-session.js",
    "mcp/stdio/hermes-session.js",
    "mcp/stdio/pi-session-pool.mjs",
    "mcp/stdio/pi-session-timeouts.mjs",
    "mcp/stdio/pi-session.js",
    "mcp/stdio/pi-terminal-frame.mjs",
    "mcp/stdio/runtimes.js",
  ],
];

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const name of fs.readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) walk(full, out);
    else if (/\.m?js$/.test(name) && !/\.test\.m?js$/.test(name)) out.push(full);
  }
  return out;
}

// Matches the SPECIFIER of a static import rather than parsing the clause. That is deliberate: this
// repo writes trailing comments on import-block openers (`import {  // note`), which defeated a
// clause parser once and hid 40 of 85 bindings.
const FROM_RE = /^[ \t]*import\s[\s\S]*?from\s*["']([^"']+)["']/gm;
const BARE_RE = /^[ \t]*import\s*["']([^"']+)["']/gm;

export function specifiersOf(source) {
  const out = new Set();
  for (const re of [FROM_RE, BARE_RE]) {
    re.lastIndex = 0;
    for (const m of source.matchAll(re)) out.add(m[1]);
  }
  return out;
}

const rel = (f) => f.slice(REPO.length + 1).replaceAll("\\", "/");

export function buildGraph() {
  const files = ROOTS.flatMap((r) => walk(path.join(REPO, r)));
  const known = new Set(files.map(rel));
  const graph = new Map();
  for (const file of files) {
    const deps = new Set();
    for (const spec of specifiersOf(fs.readFileSync(file, "utf8"))) {
      if (!spec.startsWith(".")) continue; // builtins and packages cannot cycle back into us
      const target = rel(path.resolve(path.dirname(file), spec));
      if (known.has(target)) deps.add(target);
    }
    graph.set(rel(file), deps);
  }
  return graph;
}

/** Tarjan's SCC, iterative. Every returned component has >1 member, i.e. is a genuine cycle. */
export function cyclesIn(graph) {
  const index = new Map();
  const low = new Map();
  const onStack = new Set();
  const stack = [];
  const out = [];
  let counter = 0;

  for (const root of [...graph.keys()].sort()) {
    if (index.has(root)) continue;
    index.set(root, counter);
    low.set(root, counter);
    counter += 1;
    stack.push(root);
    onStack.add(root);
    const work = [[root, [...(graph.get(root) || [])].sort()]];
    while (work.length) {
      const [node, pending] = work[work.length - 1];
      if (pending.length) {
        const dep = pending.pop();
        if (!index.has(dep)) {
          index.set(dep, counter);
          low.set(dep, counter);
          counter += 1;
          stack.push(dep);
          onStack.add(dep);
          work.push([dep, [...(graph.get(dep) || [])].sort()]);
        } else if (onStack.has(dep)) {
          low.set(node, Math.min(low.get(node), index.get(dep)));
        }
        continue;
      }
      work.pop();
      if (work.length) {
        const parent = work[work.length - 1][0];
        low.set(parent, Math.min(low.get(parent), low.get(node)));
      }
      if (low.get(node) === index.get(node)) {
        const comp = [];
        for (;;) {
          const w = stack.pop();
          onStack.delete(w);
          comp.push(w);
          if (w === node) break;
        }
        if (comp.length > 1) out.push(comp.sort());
      }
    }
  }
  return out;
}

const asKey = (comp) => comp.join("\n");

test("no import cycle beyond the one that is known and mitigated", () => {
  const found = cyclesIn(buildGraph()).map((c) => c.sort());
  const allowed = new Set(KNOWN_CYCLES.map(asKey));
  const unexpected = found.filter((c) => !allowed.has(asKey(c)));
  assert.deepEqual(
    unexpected,
    [],
    "new or changed ESM import cycle. Node does NOT throw on these — it hoists the bindings, so the " +
      "failure is a ReferenceError at call time or a silent undefined, in production. Break it by " +
      "moving the shared name DOWN into a module both sides import:\n" +
      unexpected.map((c) => "  " + c.join("\n  ")).join("\n\n"),
  );
});

test("the known cycle still exists exactly as recorded", () => {
  // A pin that silently stops matching would exempt whatever the cycle became. If this component is
  // ever genuinely broken up, DELETE the entry — do not leave it exempting nothing.
  const found = new Set(cyclesIn(buildGraph()).map((c) => asKey(c.sort())));
  for (const known of KNOWN_CYCLES) {
    assert.ok(
      found.has(asKey([...known].sort())),
      "the recorded cycle no longer matches the graph. If it was fixed, remove it from KNOWN_CYCLES; " +
        "if it merely changed shape, the mitigation in adapters/index.js was reasoned about the OLD " +
        "shape and needs re-checking.",
    );
  }
});

test("the scan is not vacuous", () => {
  const graph = buildGraph();
  const edges = [...graph.values()].reduce((n, s) => n + s.size, 0);
  assert.ok(graph.size > 150, `only ${graph.size} JS modules found — the walk is probably broken`);
  assert.ok(edges > 400, `only ${edges} relative import edges found — the parser is probably broken`);
});

test("the detector finds synthetic cycles and does not invent them", () => {
  assert.deepEqual(cyclesIn(new Map([["a", new Set(["b"])], ["b", new Set(["a"])]])), [["a", "b"]]);
  assert.deepEqual(
    cyclesIn(new Map([["x", new Set(["y"])], ["y", new Set(["z"])], ["z", new Set(["x"])]])),
    [["x", "y", "z"]],
  );
  assert.deepEqual(
    cyclesIn(new Map([["a", new Set(["b"])], ["b", new Set(["c"])], ["c", new Set()]])),
    [],
    "a chain is not a cycle",
  );
  assert.deepEqual(cyclesIn(new Map([["a", new Set(["a"])]])), [], "self-import is not a multi-module cycle");
});

test("the specifier parser survives this repo's import shapes", () => {
  // Each of these has cost real debugging time in this repo.
  assert.deepEqual([...specifiersOf('import {  // trailing comment on the opener\n  a,\n} from "./x.mjs";\n')], ["./x.mjs"]);
  assert.deepEqual([...specifiersOf('import "./side-effect.js";\n')], ["./side-effect.js"]);
  assert.deepEqual([...specifiersOf('import x from "node:fs";\n')], ["node:fs"]);
  assert.deepEqual([...specifiersOf('const s = "import y from \\"./not-real.js\\"";\n')], [],
    "a specifier inside a string literal on its own line is not an import");
  assert.deepEqual([...specifiersOf('await import("./dynamic.mjs");\n')], [],
    "a dynamic import cannot close a load-time cycle and must not be counted");
});
