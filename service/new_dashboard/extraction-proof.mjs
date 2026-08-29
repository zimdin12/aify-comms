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
/**
 * Recover the PRISTINE BODY from a declaration that wraps it — the extract-method case.
 *
 * WHY THIS EXISTS. Until now the prover could only demonstrate a RELOCATION: a whole declaration leaves
 * app.js and reappears in a module, so the span in the module is byte-identical to the span that left and
 * reconstruction just puts it back. That covers nothing where the pristine file held a bare BODY — the
 * 631-line delegated click handler at the centre of app.js is one top-level statement containing ~82
 * branch bodies, none of them a declaration, so no relocation can reach any of it. Reading that as
 * "app.js needs a redesign" confuses a limit of the TOOL with a property of the code.
 *
 * An extract-method adds exactly three things around a body: a header, a footer, and one level of
 * indentation. Each is declared and verified VERBATIM here, on the same principle as `marker` and
 * `importLine` — both of which are also executable text that a loose mask could hide an edit behind. What
 * is left after removing all three must equal the pristine body byte for byte, or the reconstruction
 * diff fails and names the slice.
 *
 * INDENTATION IS THE PART THAT IS EASY TO GET WRONG, so it is strict: every non-blank body line must
 * literally start with the declared prefix. A line that does not is a line the slice CHANGED while
 * claiming only to have moved it, and it throws rather than being silently left alone — which would
 * reconstruct to something that differs from the pristine file in a way the diff would blame on its
 * neighbour.
 */
function unwrapBody(spanLines, wrapper, name, module) {
  const header = [].concat(wrapper.header ?? []);
  const footer = [].concat(wrapper.footer ?? []);
  const indent = wrapper.indent ?? "";

  if (spanLines.length < header.length + footer.length) {
    throw new Error(`${name} in ${module} is shorter than its declared wrapper`);
  }
  header.forEach((line, k) => {
    if (spanLines[k] !== line) {
      throw new Error(`wrapper header line ${k} does not match for ${name}; the mask would hide an edit`);
    }
  });
  footer.forEach((line, k) => {
    const at = spanLines.length - footer.length + k;
    if (spanLines[at] !== line) {
      throw new Error(`wrapper footer line ${k} does not match for ${name}; the mask would hide an edit`);
    }
  });

  const body = spanLines.slice(header.length, spanLines.length - footer.length);

  // THE COMMON DIRECTION IS THE OTHER ONE, which the first version of this got wrong. A body extracted
  // from a nested block — every branch of app.js's click handler sits at 4 spaces inside `if (x) {` —
  // becomes a top-level function whose body sits at 2. The module has LESS indentation than the pristine
  // file, not more, so `dedent` declares the prefix reconstruct must ADD back.
  //
  // No verbatim check is possible in this direction: there is nothing in the module line to verify the
  // missing prefix against. That is a weaker error message, not weaker protection — a changed line still
  // fails the byte-identity comparison, which is asserted separately rather than assumed.
  const dedent = wrapper.dedent ?? "";
  if (dedent && indent) {
    throw new Error(`${name} in ${module} declares both indent and dedent, which cannot both be true`);
  }
  if (dedent) return body.map((line) => (line.trim() === "" ? line : dedent + line));

  if (!indent) return body;
  return body.map((line, k) => {
    if (line.trim() === "") return line.startsWith(indent) ? line.slice(indent.length) : line;
    if (!line.startsWith(indent)) {
      throw new Error(
        `${name} in ${module}: body line ${k} does not carry the declared indent, so it was EDITED, not `
          + `re-indented: ${JSON.stringify(line)}`,
      );
    }
    return line.slice(indent.length);
  });
}

/**
 * Undo DECLARED edits made to a module AFTER its extraction, so the reconstruction still sees the body
 * that left app.js.
 *
 * WITHOUT THIS EVERY EXTRACTED MODULE IS FROZEN FOREVER. The proof rebuilds the pre-extraction app.js
 * from the current modules, so any later change to extracted code — a bug fix, a new guard — makes the
 * reconstruction differ and the gate red. That is a far worse problem than the one the proof solves:
 * six modules and counting would become unmaintainable, and the obvious way out is to delete the gate.
 *
 * It stays honest because the edit is DECLARED, not tolerated. `was` and `now` are both matched
 * VERBATIM against the module's current text, exactly like `marker` and `importLine`, so the gate still
 * fails on any change that was not written down — and the plan entry is a permanent, reviewable record
 * of every intentional divergence from what was extracted.
 *
 * @param {string[]} body   the body recovered from the module
 * @param {Array<{was: string|string[], now: string|string[]}>} [edits]
 */
function undoEdits(body, edits, name, module) {
  if (!edits || !edits.length) return body;
  let lines = body;
  for (const edit of edits) {
    const now = [].concat(edit.now);
    const was = [].concat(edit.was);
    // AN EMPTY `now` MATCHES EVERYWHERE, so it must not be allowed to match anywhere.
    // `[].every(...)` is vacuously true, so `findIndex` answers 0 and a declared DELETION is undone
    // at the top of the declaration -- above its own `function` line. That happened, and it
    // surfaced as a byte mismatch reported three thousand lines from the cause. The fix a plan
    // needs is a line of context on both sides, which this says rather than leaving to be inferred.
    if (!now.length) {
      throw new Error(
        `${name} in ${module}: an editedSince entry has an empty \`now\`, which matches at line 0 `
          + "and would undo the edit at the top of the declaration. Give the hunk one line of "
          + "surrounding context in BOTH arrays so its anchor is unique: "
          + JSON.stringify(was[0] ?? null),
      );
    }
    const at = lines.findIndex((_, i) => now.every((l, k) => lines[i + k] === l));
    if (at === -1) {
      throw new Error(
        `${name} in ${module}: declared edit not found verbatim, so the plan and the module disagree: `
          + JSON.stringify(now[0]),
      );
    }
    // AND IT MUST MATCH ONCE. A second match means `was` lands at whichever came first, which is a
    // choice the plan never made. The seeding undo already refuses an ambiguous line; this is the
    // same rule for a declaration's edits.
    const again = lines.findIndex(
      (_, i) => i > at && now.every((l, k) => lines[i + k] === l),
    );
    if (again !== -1) {
      throw new Error(
        `${name} in ${module}: an editedSince anchor matches at lines ${at + 1} and ${again + 1}, `
          + "so which one it undoes is arbitrary. Widen the hunk with surrounding context: "
          + JSON.stringify(now[0]),
      );
    }
    lines = [...lines.slice(0, at), ...was, ...lines.slice(at + now.length)];
  }
  return lines;
}

export function reconstruct({ after, modules, extractions, carrierEdits }) {
  let lines = after.split("\n");

  // 0. undo edits made to lines that STAYED in the carrier.
  //
  // `editedSince` covers a line inside a declaration that moved out; this covers one that never
  // moved. Without it app.js's own code is frozen: every retained line must equal the fixture, so a
  // field rename in the orchestrator could not be corrected without first slicing the function out.
  //
  // Nothing is loosened. Each edit is located by its CURRENT text, verbatim, and an edit the plan
  // does not declare still reaches the byte comparison and still fails.
  lines = undoEdits(lines, carrierEdits, "app.js", "the carrier");

  // 1. remove every import the extractions added, and restore any line they replaced.
  //
  // `importBlock` handles a multi-line parenthesised import, because a nine-name import does not fit one
  // readable line — the import-readability gate on the Python side exists for exactly that reason. Each
  // line is verified verbatim so a loosened mask cannot swallow an unrelated edit.
  // REVERSE CHRONOLOGICAL, and it has to be. The plan is append-only, so `extractions` is in the order the
  // slices happened; undoing them is the opposite order. It made no difference while every slice added its
  // OWN import line — independent edits unwind in any order. The first slice to edit an import line a
  // PREVIOUS slice had already written broke it: the older entry looked for the line as it wrote it, found
  // the newer slice's version instead, and threw "import line not found verbatim". Going backwards, the
  // newest edit is undone first and each earlier entry then finds exactly the text it left.
  for (const step of [...extractions].reverse()) {
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

  // 1b. remove every SEEDING line a slice added — a statement the extraction made necessary that
  // restores no body of its own.
  //
  // The init-style slices need this and nothing else covered it. A module that takes its dependencies
  // through `initX({...})` adds one call to app.js's boot block; that line belongs to no declaration, so
  // it cannot be an item's `marker` (a marker is removed WITH a body inserted at a pristine index, and
  // there is no pristine index here — the line did not exist before the slice). Attaching it to an
  // unrelated item's marker was the workaround the first init slice used, and it put a socket's seeding
  // call under a variable declaration that has nothing to do with it.
  //
  // Verified verbatim like everything else, and REVERSE-ORDERED for the same reason as the imports.
  for (const step of [...extractions].reverse()) {
    for (const line of [].concat(step.seeding ?? []).reverse()) {
      const at = lines.indexOf(line);
      if (at === -1) throw new Error(`seeding line not found verbatim for ${step.module}: ${line}`);
      if (lines.indexOf(line, at + 1) !== -1) {
        throw new Error(`seeding line is ambiguous for ${step.module} (appears more than once): ${line}`);
      }
      lines.splice(at, 1);
    }
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
      // PROSE THAT MOVED WITH THE DECLARATION IT EXPLAINS.
      //
      // `declarationSpan` covers a declaration, not the comment above it, so a slice that takes both —
      // which is the right thing to do, a comment explaining a function is worthless without it — leaves
      // the reconstruction short by those lines. The first time this came up they were declared as an
      // `editedSince` restoring text the plan carried, which reconstructs correctly but verifies nothing:
      // reword the comment in the module and the proof still passes, because it never read it.
      //
      // `leading: n` instead takes the n lines immediately ABOVE the span FROM THE MODULE. They are
      // therefore checked like every other moved line — byte-identity does the work — and `at` points at
      // the first of them rather than at the declaration.
      const moduleLines = source.split(NL);
      const leadingCount = item.leading ?? 0;
      if (leadingCount < 0 || !Number.isInteger(leadingCount)) {
        throw new Error(`${item.name} in ${step.module}: leading must be a non-negative integer`);
      }
      if (leadingCount > span.start) {
        throw new Error(
          `${item.name} in ${step.module}: leading ${leadingCount} reaches above the top of the module`,
        );
      }
      const leading = moduleLines.slice(span.start - leadingCount, span.start);
      if (leading.some((line) => line.trim() && !line.trim().startsWith("//"))) {
        throw new Error(
          `${item.name} in ${step.module}: leading must be comment or blank lines, so it cannot silently `
            + `absorb a declaration: ${JSON.stringify(leading.find((l) => l.trim() && !l.trim().startsWith("//")))}`,
        );
      }

      items.push({
        name: item.name,
        at: item.at,
        // `marker` may be several lines: a comment explaining a move is not always one sentence, and a
        // plan that removes only its first line leaves the rest behind — which showed up as a
        // reconstruction one line too long.
        marker: item.marker == null ? [] : [].concat(item.marker),
        body: [
          ...leading,
          ...undoEdits(
            item.wrapper
              ? unwrapBody(span.text.split(NL), item.wrapper, item.name, step.module)
              : (pristineExported ? span.text : span.text.replace(/^export\s+/, "")).split(NL),
            item.editedSince,
            item.name,
            step.module,
          ),
        ],
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
      const arrow = line.indexOf("=>");
      const scanned = arrow !== -1 && !line.slice(arrow).includes("{") ? line.slice(0, arrow) : line;
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

/**
 * Keys declared twice inside one entry of the extraction plan.
 *
 * The plan is a JS object literal, so a second `editedSince:` on one entry is legal and SILENT:
 * the later key wins and the earlier declaration disappears. That happened on 2026-08-29 while
 * declaring the destructive-confirmation edits -- `unsendMessage` already carried an `editedSince`
 * three lines below its `name`, past the `at` and `marker` that sit between them, so a generator
 * checking only the following line added a second one. The undeclared edit then surfaced as a
 * whole-file byte mismatch reported thousands of lines from its cause, which is the same failure
 * shape the index-0 refusal above was built for.
 *
 * Scoped to the named plan constant, and it THROWS when that constant is absent: the test file
 * also holds inline fixtures whose objects carry a `name`, and scanning those reported four
 * entries of a fixture as duplicate-keyed. A scan that cannot find its subject must say so rather
 * than widen to whatever else it can see.
 *
 * Depth, not indentation, decides what belongs to an entry -- the plan nests two levels deep in
 * places and an indent rule read a nested `was:` as a sibling key.
 *
 * Returns `[{ name, key, count }]`, empty when the plan is well formed.
 */
export function duplicateEntryKeys(source, planName = "EXTRACTIONS") {
  // NOT declarationSpan: it counts brackets inside string literals by design, and the plan's
  // markers carry code, so its balance reaches zero 188 lines into a 3,000-line array and it
  // answers null. The plan's own shape is unambiguous instead -- it opens at its declaration and
  // closes at a `];` in column zero.
  const all = source.split(NL);
  const first = all.findIndex((l) => l.startsWith(`const ${planName} = [`));
  if (first === -1) throw new Error(`duplicateEntryKeys: no ${planName} declaration to scan`);
  const last = all.findIndex((l, i) => i > first && l === "];");
  if (last === -1) throw new Error(`duplicateEntryKeys: ${planName} is never closed at column zero`);
  const lines = all.slice(first, last + 1);
  const depths = [];
  let depth = 0;
  for (const line of lines) {
    depths.push(depth);
    for (const ch of line) {
      if (ch === "{" || ch === "[") depth += 1;
      else if (ch === "}" || ch === "]") depth -= 1;
    }
  }
  const out = [];
  for (let i = 0; i < lines.length; i += 1) {
    const m = /^\s*name:\s*"([^"]+)"/.exec(lines[i]);
    if (!m) continue;
    const own = depths[i];
    const counts = new Map();
    for (let j = i; j < lines.length && depths[j] >= own; j += 1) {
      if (depths[j] !== own) continue;
      const key = /^\s*([A-Za-z_$][\w$]*):/.exec(lines[j]);
      if (key) counts.set(key[1], (counts.get(key[1]) || 0) + 1);
    }
    for (const [key, count] of counts) if (count > 1) out.push({ name: m[1], key, count });
  }
  return out;
}
