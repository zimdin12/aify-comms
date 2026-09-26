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

/** Blank the TEXT of every template literal while keeping what is inside `${...}`.
 *
 * A SCANNER, NOT A REGEX, and the dashboard is why. `run-inspector.mjs` writes
 *
 *     ${sourceMessage ? `<button … ="${esc(messageId(sourceMessage))}">…</button>` : ''}
 *
 * — a template nested inside another template's interpolation. Extracting interpolations with
 * `/\$\{([^{}]*)\}/g` cannot span the inner braces, so `messageId` was dropped and the gate did not
 * catch the very defect it was extended here to find. Mutation caught that; the fixture alone did
 * not, because the fixture I wrote first was only one level deep.
 *
 * Blanking template literals wholesale is not an option either: `${messageId(sourceMessage)}` is a
 * genuine call. So the text goes, character by character, and the interpolations stay — tracking
 * backtick nesting and brace depth, which is the only way to know which is which.
 */
export function stripTemplateText(source) {
  let out = "";
  let i = 0;
  // Each open template pushes a frame; an interpolation inside it tracks its own brace depth.
  const stack = [];
  while (i < source.length) {
    const ch = source[i];
    const inTemplateText = stack.length > 0 && stack[stack.length - 1].depth === 0;
    if (ch === "\\" && inTemplateText) {
      out += "  ";
      i += 2;
      continue;
    }
    if (ch === "`") {
      if (inTemplateText) stack.pop();
      else stack.push({ depth: 0 });
      out += " ";
      i += 1;
      continue;
    }
    if (inTemplateText) {
      if (ch === "$" && source[i + 1] === "{") {
        stack[stack.length - 1].depth = 1;
        out += "  ";
        i += 2;
        continue;
      }
      out += ch === "\n" ? "\n" : " ";
      i += 1;
      continue;
    }
    if (stack.length) {
      const frame = stack[stack.length - 1];
      if (ch === "{") frame.depth += 1;
      else if (ch === "}") {
        frame.depth -= 1;
        if (frame.depth === 0) {
          out += " ";
          i += 1;
          continue;
        }
      }
    }
    out += ch;
    i += 1;
  }
  return out;
}

/** Text in which a bare identifier occurrence really is a USE of a binding. */
export function usableCode(source) {
  const withoutTemplateText = stripTemplateText(strip(source));
  return withoutTemplateText
    // A re-export names without binding: `export { A } from "./x.js"` is not a use of A.
    .replace(/^export\s*\{[^}]*\}\s*from\s*["'][^"']+["'];?/gm, "")
    // An alias import leaves the ORIGINAL on the line: `import { X as Y }` binds only Y.
    .replace(/([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)/g, "$2")
    // STRING CONTENT IS TEXT, NOT CODE — including, but not only, module specifiers. A quoted
    // `'./api-client.mjs'` contains `api`, and so does the URL in
    // `` `${apiOrigin}/api/v1/dashboard` ``, which is how `static-links.mjs` reported a missing
    // import of an `api` it never mentions.
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
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

// ── ANY sibling, not only one the module already imports from (0.7.1 review, T05) ────────────────
//
// The shared rule asks about siblings a module ALREADY imports from, so a module that uses `relTime`
// and imports nothing at all from `util.js` passes it. `free-names.test.mjs` asked the wider
// question -- a name used here, exported by any sibling, and neither imported nor declared -- until
// 66fd9a6e deleted it as covered. The same detector's pieces answer it; the population is every
// dashboard module in the directory listing, so a new module is in it without anyone adding it. It lives
// here since v0.7.2 because the bridge asks the same question (no-missing-sibling-imports.test.js).

const escapeName = (name) => name.replace(/\$/g, "\\$");

// Widening the population past "siblings already imported from" exposed two shapes the shared
// detector never had to tell apart, both measured on this tree as false reports (four of them):
//
//   * AN OBJECT-LITERAL KEY names a property, not the binding: `{ sessionId: String(…) }` in
//     agent-processes.mjs, `{ selectedSessionIds: new Set() }` in state.mjs. A shorthand `{ name }`
//     IS a use of the binding, so only the `key:` form is set aside.
//   * AN ARROW'S PARAMETERS with a nested default: `({ api, onError = () => {} }) =>` in
//     terminal-input.mjs. The shared parameter pattern cannot span the inner parentheses, so the
//     parameters are found here by walking back from `=>` to the matching `(`.

/** Every identifier inside an arrow function's parameter list, however deeply it nests. */
function arrowParameterNames(code) {
  const names = new Set();
  for (const arrow of code.matchAll(/\)\s*=>/g)) {
    let depth = 0;
    for (let i = arrow.index; i >= 0; i -= 1) {
      if (code[i] === ")") depth += 1;
      else if (code[i] === "(" && --depth === 0) {
        for (const token of code.slice(i + 1, arrow.index).matchAll(/[A-Za-z_$][\w$]*/g)) names.add(token[0]);
        break;
      }
    }
  }
  return names;
}

/** `text` cut at each top-level occurrence of `sep`, outside every (), [] and {}. */
function splitTopLevel(text, sep) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if ("([{".includes(c)) depth += 1;
    else if (")]}".includes(c)) depth -= 1;
    else if (c === sep && depth === 0) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

/**
 * The names a parameter list or destructuring pattern BINDS, never the tokens of a default value.
 * `listeners = () => listListeners(...)` binds `listeners` and USES `listListeners`; reading every
 * token as bound hid exactly the deleted import this gate exists to find (v0.7.2).
 */
function patternNames(text) {
  const names = [];
  for (const raw of splitTopLevel(text, ",")) {
    // The binding is everything before a top-level `=`; an arrow's `=>` in a default comes after it.
    let part = splitTopLevel(raw.trim().replace(/^\.\.\./, ""), "=")[0].trim();
    const alias = splitTopLevel(part, ":");
    if (alias.length > 1) part = alias.slice(1).join(":").trim();
    if (part.startsWith("{") && part.endsWith("}")) names.push(...patternNames(part.slice(1, -1)));
    else if (part.startsWith("[") && part.endsWith("]")) names.push(...patternNames(part.slice(1, -1)));
    else if (/^[A-Za-z_$][\w$]*$/.test(part)) names.push(part);
  }
  return names;
}

/**
 * Every identifier inside a `function`'s parameter list, found by matching parentheses FORWARD from
 * its `(`. The shared parameter pattern stops at the first inner `)`, so a destructured parameter
 * after a default like `imageOf = () => null` read as a use of a sibling's export: three false reports
 * on the bridge when this scan was pointed at it (v0.7.2). `if (...) {` is never read as parameters.
 */
function functionParameterNames(code) {
  const names = new Set();
  for (const fn of code.matchAll(/\bfunction\b[^(]*\(/g)) {
    let depth = 0;
    const open = fn.index + fn[0].length - 1;
    for (let i = open; i < code.length; i += 1) {
      if (code[i] === "(") depth += 1;
      else if (code[i] === ")" && --depth === 0) {
        for (const name of patternNames(code.slice(open + 1, i))) names.add(name);
        break;
      }
    }
  }
  return names;
}

/**
 * Every identifier inside a `const|let|var { ... } =` destructuring, found by matching braces FORWARD.
 * The shared pattern stops at the first `}`, so a default holding a block -- `nextId = (() => { ... })()`
 * in hermes-active-session.mjs -- hid the names after it (v0.7.2). Over-counting a default's tokens can
 * only suppress a report, the safe direction.
 */
function destructuredNames(code) {
  const names = new Set();
  for (const decl of code.matchAll(/\b(?:const|let|var)\s*\{/g)) {
    let depth = 0;
    const open = decl.index + decl[0].length - 1;
    for (let i = open; i < code.length; i += 1) {
      if (code[i] === "{") depth += 1;
      else if (code[i] === "}" && --depth === 0) {
        for (const name of patternNames(code.slice(open + 1, i))) names.add(name);
        break;
      }
    }
  }
  return names;
}

/** True when `name` occurs in `code` somewhere other than as an object-literal key. */
function usesName(code, name) {
  const n = escapeName(name);
  const all = code.match(new RegExp(`(?<![\\w$.])${n}(?![\\w$])`, "g"))?.length ?? 0;
  const asKey = code.match(new RegExp(`[{,]\\s*${n}\\s*:`, "g"))?.length ?? 0;
  return all > asKey;
}

/** Names `source` uses that some OTHER module in `known` exports, and that it neither imports nor declares. */
export function usedFromAnySiblingWithoutImport(file, source, known) {
  const code = usableCode(source);
  const bound = new Set([...moduleBindings(source).bound, ...arrowParameterNames(code), ...functionParameterNames(code), ...destructuredNames(code)]);
  const found = [];
  for (const [sibling, names] of known) {
    if (sibling === file) continue;
    for (const name of names) {
      if (!bound.has(name) && usesName(code, name)) found.push({ name, from: sibling });
    }
  }
  return found;
}
