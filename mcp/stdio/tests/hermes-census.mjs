// The hermes host census: every effect, shared state and import edge in the code a hermes host runs, so the move
// out of aify-comms (docs/superpowers/plans/hermes-host-to-env/, page 2) has a complete list to move by.
//
// A real parser, not a regex: the caller hands in acorn and acorn-walk, as `undeclared-names.mjs` does. The test
// takes both from the copy Node bundles (`--expose-internals`), the only JS parser on this host.
//
// THE POPULATION is rooted at the entry points the host runs (ROOTS), not at filenames. Every module reached from a
// root by a static edge is in, whatever its name. Only relative specifiers are followed; a package or builtin is an
// edge out of the population and is recorded, not walked.
//
// THE GRAMMAR, page 2's table, every occurrence counted where it sits, private closures included:
//   import / export-from / import() of a string literal ...... `import` (and `heartbeat` when the target is one)
//   import() of anything else ................................ `UNKNOWN`, which fails the census
//   child_process spawn/exec/fork, and `.kill(` .............. `process` / `kill`
//   aify-http calls, and fetch ............................... `service-http` / `http-fetch`
//   setInterval, setTimeout, setImmediate, timers/promises ... `timer`
//   new WebSocket ............................................ `ws-open`
//   fs writes ................................................ `file-write` (all of them: page 2's TEMP markers are a subset)
//   process.on / once / addListener .......................... `signal`
//   module-level let/var, and module-level new Map/Set ...... `shared-state`
//   process.env reads ........................................ `env-read`
//
// NAMES, NOT SCOPES. A binding is tracked by name across the file, and a name assigned from a tracked one is
// tracked too (`const spawn = spawnImpl || (await import("node:child_process")).spawn`). That over-counts, which
// can only add a row to review; it cannot hide one.

/** The entry points a hermes host runs (page 2, "The population"). */
export const ROOTS = Object.freeze([
  "hermes-managed-host.js",
  "hermes-daemon-cli.js",
  "hermes-endpoint.js",
  "adapters/hermes.js",
]);

export const OWNERS = Object.freeze(["comms", "aify-env", "aify-wrapper"]);
export const CLASSES = Object.freeze(["PURE_DRIVER", "SERVICE_ADAPTER", "PROCESS_OWNER", "COMPOSITION"]);

const CHILD_PROCESS = new Set(["child_process", "node:child_process"]);
const TIMER_MODULES = new Set(["timers", "node:timers", "timers/promises", "node:timers/promises"]);
const PROCESS_CALLS = new Set(["spawn", "spawnSync", "execFile", "execFileSync", "execSync", "fork"]);
const TIMER_CALLS = new Set(["setInterval", "setTimeout", "setImmediate"]);
const FETCH_CALLS = /^fetch(Impl|Fn)?$/;
const FILE_WRITES = new Set([
  "writeFileSync", "writeFile", "appendFileSync", "appendFile", "mkdirSync", "mkdir", "renameSync", "rename",
  "rmSync", "rm", "unlinkSync", "unlink", "copyFileSync", "copyFile", "openSync", "createWriteStream",
  "chmodSync", "symlinkSync", "linkSync", "utimesSync", "truncateSync", "rmdirSync", "cpSync",
]);
const SIGNAL_CALLS = new Set(["on", "once", "addListener", "prependListener"]);

const isHttpModule = (specifier) => /(^|\/)aify-http\.mjs$/.test(specifier);
const isHeartbeat = (specifier) => /heartbeat/i.test(specifier);
const literal = (node) => (node && node.type === "Literal" && typeof node.value === "string" ? node.value : null);
const isProcessEnv = (node) => node?.type === "MemberExpression" && !node.computed
  && node.object.type === "Identifier" && node.object.name === "process" && node.property.name === "env";

/** The name a function node is known by: its own id, or what it is assigned to. */
function functionName(node, parent) {
  if (node.id?.name) return node.id.name;
  if (!parent) return "<anon>";
  if (parent.type === "VariableDeclarator" && parent.id.type === "Identifier") return parent.id.name;
  if (parent.type === "AssignmentExpression") return parent.left.property?.name ?? parent.left.name ?? "<anon>";
  if ((parent.type === "Property" || parent.type === "MethodDefinition") && !parent.computed) {
    return parent.key.name ?? String(parent.key.value);
  }
  if (parent.type === "AssignmentPattern" && parent.left.type === "Identifier") return parent.left.name;
  return "<anon>";
}

const FUNCTIONS = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"]);

/** "<module>", or the chain of enclosing function names, outermost first: "runEnsureHostCli/spawnJoiningLease". */
function enclosing(ancestors) {
  const names = [];
  for (let i = 0; i < ancestors.length - 1; i += 1) {
    const node = ancestors[i];
    if (!FUNCTIONS.has(node.type)) continue;
    names.push(functionName(node, ancestors[i - 1]));
  }
  return names.length ? names.join("/") : "<module>";
}

/** Every name read in an expression subtree. */
function namesIn(node, walk) {
  const found = new Set();
  if (!node) return found;
  walk.full(node, (n) => { if (n.type === "Identifier") found.add(n.name); });
  return found;
}

/** The literal specifier of `import("x")` anywhere inside `node`, or null. */
function importedModuleIn(node, walk) {
  let found = null;
  if (!node) return found;
  walk.full(node, (n) => { if (n.type === "ImportExpression" && literal(n.source)) found = literal(n.source); });
  return found;
}

/**
 * One file's census: [{ kind, detail, symbol }] in source order, plus its import specifiers.
 *
 * `symbol` is the enclosing function chain, so a private closure is attributed to itself, not to the export that
 * holds it. `parse` and `walk` are acorn's `parse` and acorn-walk.
 */
export function censusOf(source, { parse, walk }) {
  const ast = parse(source, { ecmaVersion: "latest", sourceType: "module", allowHashBang: true, locations: true });
  const processNames = new Set();
  const timerNames = new Set();
  const httpNames = new Set();
  const occurrences = [];
  const imports = [];
  const exports = [];
  const add = (kind, detail, ancestors) => occurrences.push({ kind, detail, symbol: enclosing(ancestors) });

  // Pass 1: bindings from imports, then names assigned from tracked names, to a fixpoint.
  for (const node of ast.body) {
    if (node.type !== "ImportDeclaration") continue;
    const from = node.source.value;
    const target = CHILD_PROCESS.has(from) ? processNames : TIMER_MODULES.has(from) ? timerNames
      : isHttpModule(from) ? httpNames : null;
    if (target) for (const spec of node.specifiers) target.add(spec.local.name);
  }
  const assignments = [];
  walk.full(ast, (n) => {
    if (n.type === "VariableDeclarator" && n.id.type === "Identifier" && n.init) assignments.push([n.id.name, n.init]);
    if (n.type === "AssignmentPattern" && n.left.type === "Identifier") assignments.push([n.left.name, n.right]);
  });
  // A name is tracked when its value IS a tracked value: the binding itself, a call of it (a factory, as
  // `httpCall = makeAifyHttpCall(...)`), a branch of `||`/`??`/`?:`, an `await`, or a member of `import()` of the
  // module. A value that merely mentions one (`stop = startHeartbeat({ httpCall })`) is not.
  const carries = (node, set, isModule) => {
    if (!node) return false;
    switch (node.type) {
      case "Identifier": return set.has(node.name);
      case "CallExpression": return carries(node.callee, set, isModule);
      case "AwaitExpression": return carries(node.argument, set, isModule);
      case "LogicalExpression": return carries(node.left, set, isModule) || carries(node.right, set, isModule);
      case "ConditionalExpression": return carries(node.consequent, set, isModule) || carries(node.alternate, set, isModule);
      case "ImportExpression": return isModule(literal(node.source) ?? "");
      case "MemberExpression": return carries(node.object, set, isModule);
      default: return false;
    }
  };
  for (let changed = true; changed;) {
    changed = false;
    for (const [name, init] of assignments) {
      for (const [set, isModule] of [[processNames, (s) => CHILD_PROCESS.has(s)], [httpNames, isHttpModule]]) {
        if (!set.has(name) && carries(init, set, isModule)) { set.add(name); changed = true; }
      }
    }
  }

  // Pass 2: the occurrences.
  walk.ancestor(ast, {
    ImportDeclaration(node, _s, anc) {
      imports.push(node.source.value);
      add("import", node.source.value, anc);
      if (isHeartbeat(node.source.value)) add("heartbeat", node.source.value, anc);
    },
    ExportNamedDeclaration(node, _s, anc) {
      if (node.source) { imports.push(node.source.value); add("import", node.source.value, anc); }
      for (const spec of node.specifiers) exports.push(spec.exported.name ?? spec.exported.value);
      const decl = node.declaration;
      if (decl?.id) exports.push(decl.id.name);
      for (const d of decl?.declarations ?? []) if (d.id.type === "Identifier") exports.push(d.id.name);
    },
    ExportAllDeclaration(node, _s, anc) { imports.push(node.source.value); add("import", node.source.value, anc); },
    ImportExpression(node, _s, anc) {
      const specifier = literal(node.source);
      if (specifier === null) { add("UNKNOWN", "import() of a non-literal", anc); return; }
      imports.push(specifier);
      add("import", specifier, anc);
      if (isHeartbeat(specifier)) add("heartbeat", specifier, anc);
    },
    CallExpression(node, _s, anc) {
      const callee = node.callee;
      // A tracked spawn or service client handed to another function is where its effect is injected.
      const handed = node.arguments.flatMap((arg) => (arg.type === "ObjectExpression"
        ? arg.properties.filter((p) => p.type === "Property").map((p) => p.value)
        : [arg]));
      for (const arg of handed) {
        if (arg.type !== "Identifier") continue;
        if (processNames.has(arg.name)) add("process", `${arg.name} (passed)`, anc);
        if (httpNames.has(arg.name)) add("service-http", `${arg.name} (passed)`, anc);
      }
      if (callee.type === "Identifier") {
        const name = callee.name;
        if (PROCESS_CALLS.has(name) || (processNames.has(name) && !FETCH_CALLS.test(name))) add("process", name, anc);
        if (TIMER_CALLS.has(name) || timerNames.has(name)) add("timer", name, anc);
        if (httpNames.has(name)) add("service-http", name, anc);
        if (FETCH_CALLS.test(name)) add("http-fetch", name, anc);
        if (FILE_WRITES.has(name)) add("file-write", name, anc);
        if (name === "require") add(literal(node.arguments[0]) === null ? "UNKNOWN" : "import", literal(node.arguments[0]) ?? "require() of a non-literal", anc);
      } else if (callee.type === "MemberExpression" && !callee.computed) {
        const prop = callee.property.name;
        const objName = callee.object.type === "Identifier" ? callee.object.name : null;
        if (PROCESS_CALLS.has(prop) || (prop === "exec" && processNames.has(objName))) add("process", prop, anc);
        if (prop === "kill") add("kill", objName ? `${objName}.kill` : ".kill", anc);
        if (TIMER_CALLS.has(prop) && (objName === "globalThis" || objName === null)) add("timer", prop, anc);
        if (FILE_WRITES.has(prop)) add("file-write", prop, anc);
        if (prop === "fetch") add("http-fetch", ".fetch", anc);
        if (objName === "process" && SIGNAL_CALLS.has(prop)) add("signal", literal(node.arguments[0]) ?? "?", anc);
        if (objName && httpNames.has(objName)) add("service-http", `${objName}.${prop}`, anc);
      }
    },
    NewExpression(node, _s, anc) {
      const name = node.callee.type === "Identifier" ? node.callee.name : node.callee.property?.name;
      if (/WebSocket/.test(name ?? "")) add("ws-open", name, anc);
      if (["Map", "Set", "WeakMap"].includes(name) && enclosing(anc) === "<module>") add("shared-state", name, anc);
    },
    VariableDeclaration(node, _s, anc) {
      const parent = anc[anc.length - 2];
      const topLevel = parent.type === "Program" || (parent.type === "ExportNamedDeclaration" && anc.length === 3);
      if (node.kind === "const" || !topLevel) return;
      for (const decl of node.declarations) add("shared-state", `${node.kind} ${decl.id.name ?? "<pattern>"}`, anc);
    },
    MemberExpression(node, _s, anc) {
      if (!isProcessEnv(node.object)) return;
      add("env-read", node.computed ? (literal(node.property) ?? "[computed]") : node.property.name, anc);
    },
    VariableDeclarator(node, _s, anc) {
      if (node.id.type === "ObjectPattern" && isProcessEnv(node.init)) {
        for (const p of node.id.properties) add("env-read", p.key?.name ?? "[rest]", anc);
      }
    },
  });
  return { occurrences, imports, exports: exports.sort() };
}

/** Resolve a relative specifier from `file` (a path relative to mcp/stdio) to another population path, or null. */
export function resolveRelative(file, specifier) {
  if (!specifier.startsWith(".")) return null;
  const parts = file.split("/").slice(0, -1);
  for (const seg of specifier.split("/")) {
    if (seg === "." || seg === "") continue;
    if (seg === "..") parts.pop();
    else parts.push(seg);
  }
  return parts.join("/");
}

/**
 * The population and every file's census, walking static edges from ROOTS.
 *
 * `read(file)` returns a file's source, or null when the file does not exist. A relative edge to a missing file
 * is an UNKNOWN occurrence: the walk cannot say what it reached.
 */
export function census({ read, parse, walk, roots = ROOTS }) {
  const files = new Map();
  const via = new Map();
  const queue = [...roots];
  while (queue.length) {
    const file = queue.shift();
    if (files.has(file)) continue;
    const source = read(file);
    if (source === null) { files.set(file, { occurrences: [{ kind: "UNKNOWN", detail: "missing file", symbol: "<module>" }], imports: [] }); continue; }
    const result = { ...censusOf(source, { parse, walk }), via: via.get(file) ?? null };
    files.set(file, result);
    for (const specifier of result.imports) {
      const next = resolveRelative(file, specifier);
      if (next && !files.has(next)) { queue.push(next); if (!via.has(next)) via.set(next, file); }
    }
  }
  return files;
}

/** Occurrence counts keyed `kind|detail|symbol`, per file: the shape the table pins. */
export function countsOf(files) {
  const out = {};
  for (const [file, { occurrences }] of [...files].sort(([a], [b]) => a.localeCompare(b))) {
    const counts = {};
    for (const o of occurrences) {
      const key = `${o.kind}|${o.detail}|${o.symbol}`;
      counts[key] = (counts[key] ?? 0) + 1;
    }
    out[file] = Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)));
  }
  return out;
}

/** The class and owner the table gives one occurrence: the symbol's top-level function, else the file's default. */
export function ownerOf(table, file, symbol) {
  const entry = table.files[file];
  if (!entry) return null;
  const top = symbol.split("/")[0];
  const bySymbol = entry.symbols?.[top];
  if (bySymbol) return bySymbol;
  if (entry.class && entry.owner) return { class: entry.class, owner: entry.owner };
  return null;
}

/**
 * Everything the table does not account for, as readable lines. Empty means the census and the table agree.
 *
 * An occurrence the table's pinned counts do not hold is new (or moved), and must be classified before it can
 * pass; a pinned one no longer present is stale. An UNKNOWN always fails. Every occurrence must resolve to a
 * known class and owner.
 */
export function unaccounted(files, table) {
  const problems = [];
  const counts = countsOf(files);
  for (const [file, keys] of Object.entries(counts)) {
    const pinned = table.counts[file] ?? {};
    for (const [key, n] of Object.entries(keys)) {
      if (key.startsWith("UNKNOWN|")) problems.push(`${file}: ${key} cannot be classified`);
      if ((pinned[key] ?? 0) !== n) problems.push(`${file}: ${key} occurs ${n} time(s), the table pins ${pinned[key] ?? 0}`);
      const symbol = key.split("|").slice(2).join("|");
      const owner = ownerOf(table, file, symbol);
      if (!owner || !OWNERS.includes(owner.owner) || !CLASSES.includes(owner.class)) {
        problems.push(`${file}: ${key} has no class and owner in the table`);
      }
    }
    for (const key of Object.keys(pinned)) if (!(key in keys)) problems.push(`${file}: ${key} is pinned but no longer occurs`);
  }
  for (const file of Object.keys(table.counts)) if (!(file in counts)) problems.push(`${file} is pinned but not in the population`);
  return problems;
}
