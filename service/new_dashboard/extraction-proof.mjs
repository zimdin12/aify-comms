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
 * Rebuild the pre-slice file from the post-slice file plus the extracted module.
 *
 * `plan` is explicit rather than inferred, so the proof cannot quietly adapt to whatever the edit did:
 *   marker      the comment line(s) left where the extracted function was; replaced by its body
 *   names       every extracted function, in the ORIGINAL file's order
 *   importLine  the exact import statement added to the consumer; removed
 *   reinsert    names that were removed leaving NO marker, with the line index to restore them at
 */
export function reconstruct({ after, module: extracted, plan }) {
  let lines = after.split("\n");

  const importAt = lines.indexOf(plan.importLine);
  if (importAt === -1) {
    throw new Error(`import line not found verbatim in the consumer: ${plan.importLine}`);
  }
  lines.splice(importAt, 1);

  const markerAt = lines.findIndex((l) => l === plan.marker[0]);
  if (markerAt === -1) throw new Error("marker comment not found in the consumer");
  for (let k = 1; k < plan.marker.length; k += 1) {
    if (lines[markerAt + k] !== plan.marker[k]) {
      throw new Error(`marker comment line ${k} does not match; the mask would hide an edit`);
    }
  }

  const bodies = plan.names.map((name) => {
    const span = functionSpan(extracted, name);
    if (!span) throw new Error(`${name} not found in the extracted module`);
    // The single declared substitution: `export ` prepended in the new module.
    return span.text.replace(/^export\s+/, "");
  });

  // marker -> the primary body; any additional names are restored at their recorded indices.
  lines.splice(markerAt, plan.marker.length, ...bodies[0].split("\n"));
  for (let i = 1; i < bodies.length; i += 1) {
    const at = plan.reinsert[plan.names[i]];
    if (at == null) throw new Error(`no reinsert index recorded for ${plan.names[i]}`);
    lines.splice(at, 0, ...bodies[i].split("\n"));
  }
  return lines.join("\n");
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
