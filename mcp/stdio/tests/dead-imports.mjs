// THE dead-import detector. One rule, imported by every gate and sweep that needs it.
//
// It lived inside `no-dead-imports.test.js` and was exported from there, which worked while the only
// consumer was the bridge. `service/new_dashboard/no-dead-imports.test.mjs` importing it made the
// arrangement visible: a test file's top-level `test()` calls RUN on import, so the dashboard suite
// was executing the bridge's four tests as a side effect of borrowing one function — reporting nine
// tests where it had five, and able to fail on a BRIDGE module from inside the dashboard run.
//
// Moved here for the same reason `bridge-sources.mjs` is its own module. The rule itself is unchanged.
//
// WHY THE DETECTOR IS SHARED RATHER THAN COPIED, in its original words: "The Python side learned this
// the hard way: a sweep tool carrying its own regex deleted four LIVE imports because its copy had
// drifted from the gate's."
//
// WHAT COUNTS AS USED is any occurrence of the identifier anywhere else in the file's CODE. Comments
// and module specifiers are stripped first. The specifier matters more than it sounds: without
// stripping it, `import fs from "fs"` can never be reported, because the quoted `"fs"` counts as a
// second occurrence of the identifier.
//
// The bias is otherwise one-directional: a false POSITIVE would fail the suite on working code, so
// anything ambiguous is treated as used.

function strip(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^.*?\/\/.*$/gm, (line) => line.split("//")[0]);
}

export function deadImportsIn(text) {
  const withSpecifiers = strip(text);
  // Blank the module specifiers before counting uses, but keep them for PARSING the import statements.
  //
  // SPREAD DOTS ARE NOT PROPERTY ACCESS. The count below excludes a name preceded by `.` so that
  // `obj.name` does not look like a use of an imported `name` — but `...name(x)` is a plain call
  // whose preceding character is also a dot, so a helper used ONLY in a spread read as dead. That is
  // not hypothetical: `splitServerUrls` and `defaultFallbackServerUrls` are called exactly that way
  // in `claude-channel.js`, and this gate reported both as dead the moment they were imported rather
  // than declared locally. Deleting them on that advice would have broken the fallback URL set.
  //
  // BOTH QUOTE STYLES, everywhere. Every pattern here assumed double quotes until 2026-08-16. The
  // dashboard writes `from './util.js'`, so the detector collected NO names from any of its 59
  // modules and reported them all clean — and the same blindness hid NINE dead imports in
  // `server.js`, which writes some of its own imports single-quoted. A gate that reads nothing
  // reports the same green as a gate that found nothing.
  const src = withSpecifiers.replace(/(\bfrom\s*)["'][^"']*["']/g, '$1""').replace(/\.\.\./g, " ");
  const names = new Set();
  const addNamed = (block) => {
    for (const raw of block.split(",")) {
      const name = raw.trim().split(" as ").pop();
      if (name && /^\w+$/.test(name)) names.add(name);
    }
  };
  // Named block, optionally preceded by a default binding. The `(?:(\w+)\s*,\s*)?` is the form the
  // detector used to be blind to: `import def, { named } from "x"` matched NEITHER this pattern (it
  // required `import` to be followed directly by `{`) nor the default pattern below (it required
  // `from` directly after the name), so BOTH bindings were invisible and a dead one could never be
  // reported.
  for (const m of withSpecifiers.matchAll(/^import\s+(?:(\w+)\s*,\s*)?\{([^}]*)\}\s*from\s*["'][^"']+["'];?/gm)) {
    if (m[1]) names.add(m[1]);
    addNamed(m[2]);
  }
  for (const m of withSpecifiers.matchAll(/^import\s+(\w+)\s+from\s+["']/gm)) names.add(m[1]);
  // Namespace import. `import * as ns from "x"` was never collected at all, so an unused namespace
  // was permanently unreportable. A USED one appears again as `ns.member`, and the member-access
  // exclusion does not apply to the namespace itself — only to what follows the dot.
  for (const m of withSpecifiers.matchAll(/^import\s*\*\s*as\s+(\w+)\s+from\s+["']/gm)) names.add(m[1]);
  return [...names].filter(
    (n) => (src.match(new RegExp(`(?<![\\w$.])${n}(?![\\w])`, "g")) || []).length < 2,
  ).sort();
}
