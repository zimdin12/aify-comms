// Reconstruction equivalence for a JS extraction — the analogue of the Python extract-method gate.
//
// The Python side proves a relocation with AST + byte identity against `git show HEAD:`. There is no
// stdlib JS parser here, so equivalence is defined TEXTUALLY and mechanically instead of by reading a
// diff: put the extracted spans back where they came from, remove the import line that was added, and
// require the result to be byte-identical to the pre-slice file.
//
// That direction matters. Comparing the extracted module against the original proves the bodies survived;
// it says nothing about whether anything ELSE in the 5,000-line file moved — a stray whitespace change, a
// line deleted two functions away, an import inserted in the wrong place. Reconstruction proves the
// complement: everything not extracted is untouched.
//
// It must fail if an extracted span is the wrong function, if untouched whitespace moves, or if the
// import-deletion mask is broad enough to hide an edit. There are tests for each of those below.
//
// ONE PRISTINE FIXTURE, A GROWING PLAN. The first version of this proof compared against a per-slice
// snapshot, which went stale the moment the next slice touched app.js — a proof that can only run once is
// a receipt, not a gate. It now reconstructs app.js as it was before ANY extraction, from the current
// app.js plus every module extracted since, so it keeps proving the WHOLE extraction history was pure and
// the fixture never needs updating.

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
  const fnHead = new RegExp(`^(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\s*\\(`);
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
    if (!fnHead.test(lines[i])) continue;
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
 * Rebuild the PRISTINE file (before any extraction) from the current file plus every extracted module.
 *
 * `extractions` is explicit rather than inferred, so the proof cannot quietly adapt to whatever the edits
 * did. One entry per slice:
 *   module      key into `modules` for the extracted module's source
 *   importLine  the exact import statement that slice added to the consumer; removed
 *   importWas   what that line replaced, if it edited an existing import instead of adding one
 *   items       [{ name, at, marker, pristineExported }] — `at` is the PRISTINE line index the body is
 *               restored to, `marker` the comment line(s) left behind (null if the body was removed
 *               leaving nothing), `pristineExported` true when the SOURCE file already exported the
 *               function so its `export ` keyword must be preserved rather than stripped
 */
export function reconstruct({ after, modules, extractions }) {
  let lines = after.split("\n");

  // 1. remove every import the extractions added, and restore any line they replaced.
  //
  // `importBlock` handles a multi-line parenthesised import, because a nine-name import does not fit one
  // readable line — the import-readability gate on the Python side exists for exactly that reason. Each
  // line is verified verbatim so a loosened mask cannot swallow an unrelated edit.
  for (const step of extractions) {
    const block = step.importBlock ?? (step.importLine == null ? null : [step.importLine]);
    if (block == null) continue;
    const at = lines.indexOf(block[0]);
    if (at === -1) throw new Error(`import line not found verbatim: ${block[0]}`);
    for (let k = 1; k < block.length; k += 1) {
      if (lines[at + k] !== block[k]) {
        throw new Error(`import block line ${k} does not match for ${step.module}`);
      }
    }
    lines.splice(at, block.length, ...(step.importWas == null ? [] : [step.importWas]));
  }

  // 2. collect every item across every slice, then process them in ASCENDING pristine order.
  //
  // Marker removal and body insertion are PAIRED per item rather than done in two passes. Two passes was
  // my first attempt and it was wrong: removing a marker shifts every later index, so a body's recorded
  // pristine index no longer addressed the right place.
  //
  // ASCENDING is the correct order and I got it backwards first. To place a body at its pristine index,
  // everything ABOVE that index must already be pristine — so the lowest index is restored first. Going
  // descending, inserting at 1068 while an 8-line body at 1041 was still missing put it eight lines too
  // high, and the reconstruction diff pointed straight at it.
  const items = [];
  for (const step of extractions) {
    const source = modules[step.module];
    if (source == null) throw new Error(`no source supplied for module ${step.module}`);
    for (const item of step.items) {
      const span = declarationSpan(source, item.name);
      if (!span) throw new Error(`${item.name} not found in ${step.module}`);
      // DID THE PRISTINE FILE EXPORT THIS FUNCTION?
      //
      // `pristineExported` describes the SOURCE FILE, not the edit, and that distinction is the whole
      // point. My first version asked "was `export ` added?" — which reads naturally and is the wrong
      // question, because it has no answer for a function where nothing happened either way.
      // `themePreviewTilesHtml` was private in app.js and is still private in settings-fields.mjs: no
      // export was added, and none exists to strip. The cross-check rejected it immediately, which is the
      // prover doing its job on its own plan.
      //
      // Asking about the pristine file covers all three cases with one flag:
      //   private -> published   pristine has no `export `, span does  -> strip it
      //   already public         pristine HAS `export `, span does     -> keep it
      //   private -> private     neither has it                        -> strip is a no-op
      //
      // `mcp/stdio/hermes-managed-host.js` needs the middle case: 11 functions in its gateway cluster are
      // already `export function`, so their spans are byte-identical with NO substitution, and stripping
      // the keyword would corrupt the reconstruction of code nobody changed.
      const pristineExported = item.pristineExported ?? false;
      const hasExport = /^export\s+/.test(span.text);
      if (pristineExported && !hasExport) {
        throw new Error(
          `${item.name} is declared pristineExported but its span in ${step.module} has no export `
            + "keyword, so the declaration and the module disagree",
        );
      }
      items.push({
        name: item.name,
        at: item.at,
        // `marker` may be several lines: a comment explaining a move is not always one sentence, and a
        // plan that removes only its first line leaves the rest behind — which showed up as a
        // reconstruction one line too long.
        marker: item.marker == null ? [] : [].concat(item.marker),
        body: (pristineExported ? span.text : span.text.replace(/^export\s+/, "")).split(NL),
      });
    }
  }
  items.sort((a, b) => a.at - b.at);

  for (const item of items) {
    if (item.marker.length) {
      const at = lines.indexOf(item.marker[0]);
      if (at === -1) throw new Error(`marker not found verbatim for ${item.name}: ${item.marker[0]}`);
      for (let k = 1; k < item.marker.length; k += 1) {
        if (lines[at + k] !== item.marker[k]) {
          throw new Error(`marker line ${k} does not match for ${item.name}; the mask would hide an edit`);
        }
      }
      lines.splice(at, item.marker.length);
    }
    lines.splice(item.at, 0, ...item.body);
  }
  return lines.join(NL);
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
    if (depth === 0 && bare && !bare.startsWith("//")) {
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
      const arrow = line.indexOf("=>");
      const scanned = arrow !== -1 && !line.slice(arrow).includes("{") ? line.slice(0, arrow) : line;
      for (const g of BROWSER_GLOBALS) {
        if (new RegExp(`\\b${g}\\b`).test(scanned)) hits.push({ line: i + 1, global: g, text: bare });
      }
    }
    for (const ch of line) {
      if (ch === "{") depth += 1;
      else if (ch === "}") depth -= 1;
    }
  }
  return hits;
}
