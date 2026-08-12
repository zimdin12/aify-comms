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
 *   items       [{ name, at, marker }] — `at` is the PRISTINE line index the body is restored to,
 *               `marker` the comment line left behind (null if the body was removed leaving nothing)
 */
export function reconstruct({ after, modules, extractions }) {
  let lines = after.split("\n");

  // 1. remove every import line the extractions added, and restore any line they replaced.
  for (const step of extractions) {
    if (step.importLine != null) {
      const at = lines.indexOf(step.importLine);
      if (at === -1) throw new Error(`import line not found verbatim: ${step.importLine}`);
      lines.splice(at, 1, ...(step.importWas == null ? [] : [step.importWas]));
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
      const span = functionSpan(source, item.name);
      if (!span) throw new Error(`${item.name} not found in ${step.module}`);
      items.push({
        name: item.name,
        at: item.at,
        // `marker` may be several lines: a comment explaining a move is not always one sentence, and a
        // plan that removes only its first line leaves the rest behind — which showed up as a
        // reconstruction one line too long.
        marker: item.marker == null ? [] : [].concat(item.marker),
        // The single declared substitution: `export ` prepended in the extracted module.
        body: span.text.replace(/^export\s+/, "").split(NL),
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
      for (const g of BROWSER_GLOBALS) {
        if (new RegExp(`\\b${g}\\b`).test(line)) hits.push({ line: i + 1, global: g, text: bare });
      }
    }
    for (const ch of line) {
      if (ch === "{") depth += 1;
      else if (ch === "}") depth -= 1;
    }
  }
  return hits;
}
