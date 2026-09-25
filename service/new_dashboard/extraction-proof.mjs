// JS parsing helpers shared by gates in both trees.
//
// `declarationSpan` / `functionSpan` locate a declaration by name with this repo's own parser, and
// `moduleScopeBrowserRefs` finds browser globals read at import time. Bridge and dashboard tests import
// them from here. The app.js reconstruction prover that gave this file its name was retired in v0.7,
// with its fixture, plan and last importer.

const NL = String.fromCharCode(10);

/**
 * Locate a top-level DECLARATION span by name — a `function` by brace matching, or a `const`/`let`/`var` by
 * running to the terminating semicolon.
 *
 * v0.5.4: the gateway slice moves 8 constants as well as 15 functions, and a function-only locator cannot
 * prove a constant relocation at all. `functionSpan` is kept as the name every existing caller uses and now
 * delegates, so the three app.js slices keep their exact behaviour.
 */
/** The code part of a line: everything before an UNQUOTED `//`.
 *
 * Needed because a declaration may end `...; // note`, which does not end in a semicolon and so never
 * satisfied the terminator test below -- `declarationSpan` then ran past the declaration and, for a
 * const at the end of a module, returned null. Quote state is tracked rather than splitting on the
 * first `//`, because `const u = 'http://x';` would otherwise lose its terminator too.
 */
function codeBeforeComment(line) {
  let quote = null;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quote) {
      if (ch === "\\") i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") { quote = ch; continue; }
    if (ch === "/" && line[i + 1] === "/") return line.slice(0, i);
  }
  return line;
}

export function declarationSpan(source, name) {
  const lines = source.split(NL);
  // WHICH SPELLINGS THIS MATCHES, stated because the ones it does NOT are invisible from a caller's
  // point of view: a miss returns null, and null reads as "no such declaration" rather than "this
  // parser cannot see that form". Until 2026-08-16 `class` was such a form, and this is the parser
  // the repo mandates for every JS measurement — so the five session classes in the bridge
  // (codex-session, hermes-session, hermes-managed-gateway-session, pi-session, terminal-runtime)
  // measured as absent. I recorded "declarationSpan presumably cannot span the big class
  // declarations" while measuring those files and moved on; that guess was right and should have
  // been this fix.
  //
  // `default` and `*` are accepted too. Neither appears in the tree today, and both are one token
  // wide — the cost of covering them is smaller than the cost of the next person meeting a null.
  const fnHead = new RegExp(
    `^(?:export\\s+)?(?:default\\s+)?(?:async\\s+)?function(?:\\s+|\\s*\\*\\s*)${name}\\s*\\(`,
  );
  const classHead = new RegExp(`^(?:export\\s+)?(?:default\\s+)?class\\s+${name}\\b`);
  const varHead = new RegExp(`^(?:export\\s+)?(?:const|let|var)\\s+${name}\\b`);
  for (let i = 0; i < lines.length; i += 1) {
    if (varHead.test(lines[i])) {
      // A declaration may span lines, and "run to the first line ending in a semicolon" is WRONG — it works
      // for a multi-line `Math.max(...)` spread over four lines by luck, and breaks on an IIFE, whose body
      // contains its own statements:
      //
      //   const X = (() => {
      //     const raw = Number(...);      <-- first line ending in ';', and not the end of the declaration
      //     ...
      //   })();
      //
      // So terminate on BALANCE: the first line where every bracket opened since the start has closed AND
      // the line ends with a semicolon. Found by truncating a real constant mid-IIFE during the
      // active-session slice.
      let depth = 0;
      for (let j = i; j < lines.length; j += 1) {
        for (const ch of lines[j]) {
          if (ch === "(" || ch === "{" || ch === "[") depth += 1;
          else if (ch === ")" || ch === "}" || ch === "]") depth -= 1;
        }
        if (depth <= 0 && codeBeforeComment(lines[j]).trimEnd().endsWith(";")) {
          return { start: i, end: j, text: lines.slice(i, j + 1).join(NL) };
        }
      }
      return null;
    }
    // A class body terminates on brace balance exactly as a function body does — the head differs,
    // the span logic does not — so the two share this branch rather than growing a second copy.
    if (!fnHead.test(lines[i]) && !classHead.test(lines[i])) continue;
    let depth = 0;
    for (let j = i; j < lines.length; j += 1) {
      for (const ch of lines[j]) {
        if (ch === "{") depth += 1;
        else if (ch === "}") depth -= 1;
      }
      if (depth === 0 && lines.slice(i, j + 1).join(NL).includes("{")) {
        return { start: i, end: j, text: lines.slice(i, j + 1).join(NL) };
      }
    }
  }
  return null;
}

/** Locate a top-level `function NAME(...) {` ... `}` span by brace matching from column 0. */
export function functionSpan(source, name) {
  const lines = source.split("\n");
  const head = new RegExp(`^(?:async\\s+)?(?:export\\s+)?function\\s+${name}\\s*\\(`);
  for (let i = 0; i < lines.length; i += 1) {
    if (!head.test(lines[i])) continue;
    let depth = 0;
    for (let j = i; j < lines.length; j += 1) {
      for (const ch of lines[j]) {
        if (ch === "{") depth += 1;
        else if (ch === "}") depth -= 1;
      }
      if (depth === 0 && lines.slice(i, j + 1).join("\n").includes("{")) {
        return { start: i, end: j, text: lines.slice(i, j + 1).join("\n") };
      }
    }
  }
  return null;
}

/**
 * A module is IMPORT-SAFE when it has no module-scope reference to a browser global or to an alias of
 * one. This is what makes an extracted module testable while `app.js` is not, so it is asserted rather
 * than assumed — the first slice must have zero, and a later impure slice must declare its own.
 */
export const BROWSER_GLOBALS = [
  "document", "window", "location", "navigator",
  "localStorage", "sessionStorage", "alert", "history", "fetch", "WebSocket",
];

export function moduleScopeBrowserRefs(source) {
  const lines = source.split("\n");
  const hits = [];
  let depth = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const bare = line.trim();
    // BLOCK COMMENTS COUNT AS COMMENTS TOO. `//` was the only form excluded, so a JSDoc block above a
    // module-scope declaration — the natural place to EXPLAIN why a global is guarded — was scanned as
    // code. `boot-wiring.mjs` was reported unimportable for a line reading "…because localStorage
    // THROWS in private mode", which is prose about the very care that makes it importable.
    const inComment = bare.startsWith("//") || bare.startsWith("/*") || bare.startsWith("*");
    if (depth === 0 && bare && !inComment) {
      // WHAT THIS ASKS is whether a browser global is touched WHILE THE MODULE EVALUATES -- that is
      // what makes a module unimportable outside a browser. It is not asking whether the word appears
      // on a line at depth 0.
      //
      // A braceless arrow body is the whole difference. `const byId = (id) => document.getElementById(id);`
      // reads `document` only when CALLED, so the module imports fine in Node -- but the brace-depth
      // counter cannot see that, because the body never opens a block. Flagging it reports a module as
      // unimportable when it is not, and the only way to satisfy the check would be to reword the moved
      // declaration, breaking the byte-identity the reconstruction proof depends on. Fixing a wrong
      // check beats rewording correct code to please it.
      //
      // Braced bodies are already excluded by the depth counter, on their own lines. Everything after a
      // braceless `=>` is deferred for the same reason and is not scanned. Over-strict elsewhere is the
      // safe direction: this can only fail a module that was importable, never pass one that is not.
      // A MODULE SPECIFIER IS NOT A REFERENCE. `import { X } from './message-history.mjs';` matches
      // /\bhistory\b/ inside the PATH, and an import specifier dereferences nothing — the statement is
      // declarative and hoisted. It reported `refresh-cycle.mjs` as unimportable for importing a module
      // whose FILENAME contains a browser global, which no amount of care in that module could fix.
      //
      // ONLY THE QUOTED PATH IS REMOVED, not the line. `import x from './a.mjs'; history.back();` is
      // legal on one line and must still be caught; the test below pins that, so this exemption cannot
      // widen into "an import line is never scanned".
      const withoutSpecifier = /^(?:import|export)\b/.test(bare)
        ? line.replace(/(\bfrom\s*|^\s*import\s*)(['"])[^'"]*\2/, "$1$2$2")
        : line;
      const arrow = withoutSpecifier.indexOf("=>");
      const scanned = arrow !== -1 && !withoutSpecifier.slice(arrow).includes("{")
        ? withoutSpecifier.slice(0, arrow)
        : withoutSpecifier;
      for (const g of BROWSER_GLOBALS) {
        if (!new RegExp(`\\b${g}\\b`).test(scanned)) continue;
        // `typeof X !== 'undefined' ? X : null` does NOT run browser code on import: `typeof` is the one
        // reference that never throws on an undeclared name, and the bare use sits in a branch that only
        // evaluates when the global exists. Flagging it reported an importable module as unimportable.
        //
        // The guard must name THIS global on THIS line. A line that guards one and dereferences another
        // unguarded is still a hit -- that case is asserted in the tests, and it is what keeps the
        // exemption from becoming a blanket pass for any line containing the word `typeof`.
        const guarded = new RegExp(`typeof\\s+${g}\\s*[!=]==\\s*["']undefined["']`).test(scanned);
        if (guarded) continue;
        hits.push({ line: i + 1, global: g, text: bare });
      }
    }
    for (const ch of line) {
      if (ch === "{") depth += 1;
      else if (ch === "}") depth -= 1;
    }
  }
  return hits;
}
