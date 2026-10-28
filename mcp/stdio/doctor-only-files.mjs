// WHICH BRIDGE FILES ONLY THE DOCTOR LOADS -- derived from the imports, never listed.
//
// `bridge-current` counts commits since a running bridge's build that touched mcp/stdio, and its remedy is a relaunch,
// which reaps managed workers. A commit that changed only the doctor asked for that: after 6143c3f9, 16 live bridges
// read "running older code" though none loads a changed file. A name pattern cannot tell them apart (`notify-check.js`
// is a runtime hook named like a `*-check.mjs` doctor module), so the set comes from the imports: what `doctor.js`
// reaches, less what any other top-level file reaches. A file nothing imports stays runtime, so a new entry point is
// runtime by default.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Static imports (side-effect ones included), dynamic import() calls and require() calls of a relative path, in either
// quote. A missed import could only make a shared file look doctor-only, the false-green direction, so this reads every
// form a module here uses.
// (Two other walkers find the doctor's files by regex, so a quoted example path in this comment would send them to it.)
const LOCAL_IMPORT = /(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*|\brequire\s*\(\s*)["'](\.{1,2}\/[^"']+)["']/g;

function closure(entry, dir) {
  const seen = new Set();
  const walk = (file) => {
    if (seen.has(file) || !existsSync(file)) return;
    seen.add(file);
    for (const [, spec] of readFileSync(file, "utf-8").matchAll(LOCAL_IMPORT)) walk(path.resolve(path.dirname(file), spec));
  };
  walk(path.resolve(dir, entry));
  return seen;
}

/** The mcp/stdio files, relative to `dir`, that only the doctor loads: `doctor.js` and what only it reaches. */
export function doctorOnlyFiles(dir) {
  const doctor = closure("doctor.js", dir);
  const runtime = new Set();
  for (const name of readdirSync(dir)) {
    const file = path.resolve(dir, name);
    if (!/\.(m?js|cjs)$/.test(name) || doctor.has(file)) continue;
    for (const reached of closure(name, dir)) runtime.add(reached);
  }
  return [...doctor].filter((file) => !runtime.has(file)).map((file) => path.relative(dir, file).split(path.sep).join("/")).sort();
}
