// THE UNDEFINED-DEFAULT-ARGUMENT DETECTOR. Its own module, for the reason `missing-imports.mjs`
// gives: a test file's `test()` calls run on import, so borrowing one function from it executes its
// suite as a side effect.
//
// WHAT IT DECIDES: a parameter default that names something the module does not define. That is a
// ReferenceError thrown at every call site that omits the argument -- which, in this repo, is every
// PRODUCTION call site, because tests inject.
//
// WHY IT IS ITS OWN QUESTION, and not covered by the missing-sibling-import gate next door:
//
//   1. `moduleBindings` deliberately treats EVERY identifier in a parameter list as bound. Its
//      comment argues that over-counting "can only SUPPRESS a report, which is the safe direction".
//      That is right for a parameter's NAME and wrong for a default's VALUE: a name is a binding, a
//      value is a use. Under that rule `(spawnSync = nodeSpawnSync)` binds both, so no default can
//      ever be reported.
//   2. That gate only reports a name EXPORTED BY A SIBLING the module already imports from. A default
//      naming something exported by nothing -- a deleted alias, a renamed import -- is out of its
//      scope by construction.
//
// SCOPE, stated so a zero from it is readable: it judges a default whose value is an IDENTIFIER
// (`= foo`, `= foo()`, `= foo.bar`). A default that is a literal, an object, an array, or an
// arrow body is not judged, because there is no bare name to resolve.
import { usableCode } from "./missing-imports.mjs";

// Names that resolve without the module defining them. Deliberately short: a name wrongly listed here
// is a defect this gate can no longer see.
export const AMBIENT = new Set([
  "undefined", "null", "true", "false", "NaN", "Infinity", "arguments", "this",
  "process", "console", "globalThis", "Buffer", "URL", "URLSearchParams", "TextDecoder", "TextEncoder",
  "Math", "Date", "JSON", "Object", "Array", "String", "Number", "Boolean", "BigInt", "Symbol",
  "Set", "Map", "WeakSet", "WeakMap", "Promise", "RegExp", "Proxy", "Reflect", "Error", "TypeError",
  "RangeError", "AbortController", "AbortSignal", "Intl", "structuredClone", "queueMicrotask",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval", "setImmediate", "fetch",
]);

// OPERATORS THAT CONTAIN AN `=`. Matching a bare `=` reads the one inside `>=`, `!==` and `+=` as an
// assignment, which reported `counter.count >= grace` as a default naming `grace`. Five of the
// thirteen false positives on this scan's first run were this.
const NOT_AN_ASSIGNMENT = new Set(["!", "<", ">", "=", "+", "-", "*", "/", "%", "&", "|", "^", "?"]);

// A KEYWORD IS NOT A NAME. `= new Foo()`, `= async () => {}` and `= typeof X` each put a reserved
// word where an identifier would be; resolving it finds nothing and reports the whole default.
const KEYWORDS = new Set([
  "new", "async", "await", "function", "typeof", "void", "delete", "yield", "class", "this", "super",
]);

// A PAREN AFTER ONE OF THESE OPENS A CONDITION, NOT A PARAMETER LIST. `if (a >= b) {` is followed by
// `{` exactly like a function header is, which is how conditions reached this scan at all.
const NOT_A_PARAMETER_LIST = /\b(if|while|for|switch|catch|with)\s*$/;

/** Every name the module itself defines: imported, declared, or the name of a function or class. */
export function definedNames(source) {
  const code = usableCode(source);
  const names = new Set();
  for (const m of code.matchAll(/^import\s+(?:([\w$]+)\s*,\s*)?\{([^}]*)\}\s*from/gm)) {
    if (m[1]) names.add(m[1]);
    for (const raw of m[2].split(",")) {
      const local = raw.trim().split(/\s+as\s+/).pop().trim();
      if (local) names.add(local);
    }
  }
  for (const m of code.matchAll(/^import\s+([\w$]+)\s+from/gm)) names.add(m[1]);
  for (const m of code.matchAll(/^import\s*\*\s*as\s+([\w$]+)\s+from/gm)) names.add(m[1]);
  for (const m of code.matchAll(/\b(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  for (const m of code.matchAll(/\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  // Destructured declarations: `const { a, b: c } = x`.
  for (const m of code.matchAll(/(?:const|let|var)\s*\{([^}]*)\}\s*=/g)) {
    for (const raw of m[1].split(",")) {
      const local = raw.trim().split(":").pop().trim().split("=")[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(local)) names.add(local);
    }
  }
  return names;
}

/** The text between the parens of every parameter list, by balanced-paren scan. */
export function parameterLists(code) {
  const lists = [];
  // A parameter list opens after `function name`, `function`, a method name, or before `=>`.
  for (let i = 0; i < code.length; i += 1) {
    if (code[i] !== "(") continue;
    let depth = 0;
    let end = -1;
    for (let j = i; j < code.length; j += 1) {
      if (code[j] === "(") depth += 1;
      else if (code[j] === ")") {
        depth -= 1;
        if (depth === 0) { end = j; break; }
      }
    }
    if (end === -1) continue;
    const after = code.slice(end + 1, end + 40);
    // A parameter list is followed by a body or an arrow. Anything else is a CALL's argument list,
    // where `a = b` is an assignment expression and `b` is an ordinary use the other gates cover.
    if (!/^\s*(\{|=>)/.test(after)) { i = end; continue; }
    // ...and a condition is followed by a body too. `if (attempts >= threshold) {` is not a signature.
    if (NOT_A_PARAMETER_LIST.test(code.slice(Math.max(0, i - 12), i))) { i = end; continue; }
    lists.push(code.slice(i + 1, end));
    i = end;
  }
  return lists;
}

/** Names a parameter list USES (a default's value) and names it BINDS (the parameters themselves). */
export function usesAndBindings(paramText) {
  const uses = new Set();
  const bindings = new Set();
  // A default's value: the identifier immediately after a REAL `=`. `= foo`, `= foo()` and
  // `= foo.bar` all resolve `foo` at call time; `>=` and `!==` are not assignments at all.
  for (const m of paramText.matchAll(/(.?)=(=?)\s*([A-Za-z_$][\w$]*)/g)) {
    const [, before, doubled, name] = m;
    if (doubled || NOT_AN_ASSIGNMENT.has(before)) continue;
    if (KEYWORDS.has(name)) continue;
    uses.add(name);
  }
  // Everything else in the list is a binding. Over-counting bindings is safe HERE, unlike in
  // `moduleBindings`, because a use is only ever collected from the right-hand side of an `=`.
  const rhs = paramText.replace(/=\s*[A-Za-z_$][\w$]*/g, "= ");
  for (const m of rhs.matchAll(/[A-Za-z_$][\w$]*/g)) bindings.add(m[0]);
  return { uses, bindings };
}

/**
 * Defaults in this source that name something nothing defines.
 * @returns {Array<{param: string, value: string}>}  -- `param` is the list's text, trimmed.
 */
export function undefinedDefaultArguments(source) {
  const code = usableCode(source);
  const defined = definedNames(source);
  const found = [];
  for (const list of parameterLists(code)) {
    const { uses, bindings } = usesAndBindings(list);
    for (const use of uses) {
      if (AMBIENT.has(use) || defined.has(use) || bindings.has(use)) continue;
      found.push({ param: list.trim().replace(/\s+/g, " ").slice(0, 120), value: use });
    }
  }
  return found;
}
