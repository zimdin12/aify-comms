// THE missing-sibling-import detector. Its own module, not an export of the test file.
//
// `dead-imports.mjs` records why: a test file's top-level `test()` calls RUN on import, so borrowing
// one function from it executes its suite as a side effect — and while debugging this detector I did
// exactly that and got a TAP dump instead of an answer.
//
// WHAT IT DECIDES: a name that is used in a module, is exported by a module that module ALREADY
// imports from, and is neither imported nor declared there. That is a ReferenceError waiting for its
// branch to run, and `node --check` cannot see it.

import path from "node:path";

// LINE COMMENTS FIRST, THEN BLOCK COMMENTS, and the order is a bug fix rather than a preference.
//
// The other way round, a `/*` written INSIDE a `//` comment opens a phantom block-comment span. It
// is not hypothetical prose: `doctor-predicates.js` says "an AST scan of non-test `service/**`" in a
// line comment, and that glob's `/*` swallowed the next 2,023 characters — including the real
// `export const SERVICE_RUNTIME_PATHS`. So this module could not see an export that was plainly
// there, and the file it was hiding is the one whose missing import crashed `aify-comms doctor`.
// Two bridge modules are affected today (`claude-turn-end-detector.js` loses 1,198 chars, this one
// 200); both hold analysis-relevant code inside the swallowed span.
function strip(text) {
  return text.replace(/^.*?\/\/.*$/gm, (line) => line.split("//")[0]).replace(/\/\*[\s\S]*?\*\//g, "");
}

export function exportedNames(source) {
  const code = strip(source);
  const names = new Set();
  for (const m of code.matchAll(/^export\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
  for (const m of code.matchAll(/^export\s+(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
  for (const m of code.matchAll(/^export\s*\{([^}]*)\}/gm)) {
    for (const raw of m[1].split(",")) {
      const name = raw.trim().split(/\s+as\s+/).pop().trim();
      if (/^[A-Za-z_$][\w$]*$/.test(name)) names.add(name);
    }
  }
  return names;
}

/** The module's own bindings (imported or declared) and the relative specifiers it imports from. */
export function moduleBindings(source) {
  const code = strip(source);
  const bound = new Set();
  const specifiers = new Set();
  for (const m of code.matchAll(/^import\s+(?:([\w$]+)\s*,\s*)?\{([^}]*)\}\s*from\s*["']([^"']+)["']/gm)) {
    if (m[1]) bound.add(m[1]);
    for (const raw of m[2].split(",")) {
      const name = raw.trim().split(/\s+as\s+/).pop().trim();
      if (name) bound.add(name);
    }
    specifiers.add(m[3]);
  }
  for (const m of code.matchAll(/^import\s+([\w$]+)\s+from\s+["']([^"']+)["']/gm)) {
    bound.add(m[1]);
    specifiers.add(m[2]);
  }
  for (const m of code.matchAll(/^import\s*\*\s*as\s+([\w$]+)\s+from\s+["']([^"']+)["']/gm)) {
    bound.add(m[1]);
    specifiers.add(m[2]);
  }
  // Declarations, deliberately over-broad: a name this module defines anywhere, in any scope, is not
  // a missing import. Over-counting here can only SUPPRESS a report, which is the safe direction for
  // a gate that must not cry wolf on working code.
  for (const m of code.matchAll(/\b(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)/g)) bound.add(m[1]);
  for (const m of code.matchAll(/\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)/g)) bound.add(m[1]);
  for (const m of code.matchAll(/(?:const|let|var)\s*\{([^}]*)\}\s*=/g)) {
    for (const raw of m[1].split(",")) {
      const name = raw.trim().split(":").pop().trim().split("=")[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(name)) bound.add(name);
    }
  }
  for (const m of code.matchAll(/\(([^()]{0,400})\)\s*(?:=>|\{)/g)) {
    for (const token of m[1].matchAll(/[A-Za-z_$][\w$]*/g)) bound.add(token[0]);
  }
  return { bound, specifiers };
}

/** Text in which a bare identifier occurrence really is a USE of a binding. */
export function usableCode(source) {
  return strip(source)
    // A re-export names without binding: `export { A } from "./x.js"` is not a use of A.
    .replace(/^export\s*\{[^}]*\}\s*from\s*["'][^"']+["'];?/gm, "")
    // An alias import leaves the ORIGINAL on the line: `import { X as Y }` binds only Y.
    .replace(/([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)/g, "$2")
    // A specifier is text: `'./api-client.mjs'` contains `api`.
    .replace(/(\bfrom\s*)["'][^"']*["']/g, '$1""')
    // SPREAD DOTS, and I wrote this bug into the detector before I fixed it. The use-scan excludes a
    // name preceded by `.` so `obj.name` is not a use of an imported `name` — and `...NAME` puts a
    // dot there too. That is precisely why `SERVICE_RUNTIME_PATHS` was deleted from `doctor.js` in
    // the first place, and my first version of THIS file could not see the fixture reproducing it.
    // `dead-imports.mjs` blanks them for the same reason.
    .replace(/\.\.\./g, " ");
}

export function missingSiblingImports(file, source, exportsByFile) {
  const code = usableCode(source);
  const { bound, specifiers } = moduleBindings(source);
  const dir = path.posix.dirname(file);
  const found = [];
  for (const specifier of specifiers) {
    if (!specifier.startsWith(".")) continue;
    const target = path.posix.normalize(path.posix.join(dir, specifier));
    const siblingExports = exportsByFile.get(target);
    if (!siblingExports) continue;
    for (const name of siblingExports) {
      if (bound.has(name)) continue;
      const used = new RegExp(`(?<![\\w$.])${name}(?![\\w$])`, "g");
      if (used.test(code)) found.push({ name, from: specifier });
    }
  }
  return found;
}
