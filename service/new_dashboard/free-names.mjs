// Which names does a module USE that it never declares or imports?
//
// Written after an extraction shipped a module missing four imports. `node --check` passed (it only
// parses), the extraction proof passed (it compares the byte-identity of MOVED SPANS, and a module's
// import header is not part of any span), and all three suites passed — because the four names sit on
// paths a unit test does not walk: one is only read when an operator clicks Interrupt. In a browser
// that is a ReferenceError at click time, with no test that could have failed first.
//
// The subtle one is why it was missed by hand: `runPendingControlCount` appears ONLY inside a template
// literal's `${...}`. A scanner that blanks whole template literals — which is the obvious way to avoid
// matching words in HTML markup — erases the reference and reports the module clean. So interpolations
// are kept and only the literal TEXT of a template is blanked.
//
// This is deliberately a heuristic and not a parser. It over-declares (every destructured name, every
// parameter) so that it under-reports rather than crying wolf, and a name is only reported if it is
// used in call or member position somewhere — an object-literal key that happens to share a name with
// nothing is not a finding.

/**
 * Names `source` uses but neither declares nor imports, excluding standard globals.
 * @returns {string[]} sorted, empty when the module is self-contained
 */
export function freeNames(src) {
  // Blank comments and string bodies, but KEEP template interpolations — a name that only appears
  // inside `${...}` is still a real reference, and blanking whole templates is how one was missed.
  let s = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  // TEMPLATES BEFORE QUOTES, and this order is not cosmetic. Template TEXT contains apostrophes —
  // "the peer's messages", "don't" — and blanking single quotes first pairs one of those with the next
  // apostrophe anywhere later in the file and deletes everything between. On chat.js that swallowed the
  // line destructuring nine dependencies, so every one of them was reported as a missing import.
  s = s.replace(/`(?:\\.|[^`\\])*`/g, (lit) => {
    const parts = [...lit.matchAll(/\$\{([\s\S]*?)\}/g)].map((m) => m[1]);
    return parts.length ? `(${parts.join(",")})` : "''";
  });
  // …and quotes are LINE-SCOPED for the same reason: a JS string literal cannot contain a raw newline,
  // so an unpaired quote that survives the pass above can only ever eat to end of line.
  s = s
    .replace(/'(?:\\.|[^'\\\n])*'/g, "''")
    .replace(/"(?:\\.|[^"\\\n])*"/g, '""');
  // Regex literals would confuse the identifier scan far less than they'd cost to parse; leave them.

  const declared = new Set();
  const add = (n) => n && declared.add(n);
  for (const m of s.matchAll(/import\s+\{([\s\S]*?)\}\s+from/g))
    for (let n of m[1].split(",")) { n = n.trim(); if (!n) continue; const as = n.match(/\bas\s+(\S+)$/); add(as ? as[1] : n); }
  for (const m of s.matchAll(/import\s+(?:\*\s+as\s+)?([A-Za-z_$][\w$]*)\s+from/g)) add(m[1]);
  for (const m of s.matchAll(/(?:^|[\s;{(])(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) add(m[1]);
  for (const m of s.matchAll(/(?:^|[\s;{(])(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/g)) add(m[1]);
  for (const m of s.matchAll(/\bclass\s+([A-Za-z_$][\w$]*)/g)) add(m[1]);
  for (const m of s.matchAll(/\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g)) add(m[1]);
  // destructuring + parameter lists: any identifier inside ( ) or { } that precedes => or a function body
  for (const m of s.matchAll(/(?:\(([^()]*)\)|([A-Za-z_$][\w$]*))\s*=>/g)) {
    for (const p of (m[1] ?? m[2] ?? "").split(",")) { const n = p.trim().replace(/^\.\.\./, "").split(/[\s=:]/)[0]; if (/^[A-Za-z_$][\w$]*$/.test(n)) add(n); }
  }
  for (const m of s.matchAll(/(?:async\s+)?function[^(]*\(([\s\S]*?)\)\s*\{/g)) {
    for (const p of m[1].replace(/[{}[\]]/g, " ").split(",")) { const n = p.trim().replace(/^\.\.\./, "").split(/[\s=:]/)[0]; if (/^[A-Za-z_$][\w$]*$/.test(n)) add(n); }
  }
  for (const m of s.matchAll(/(?:const|let|var)\s*[{[]([^}\]]*)[}\]]/g)) {
    for (const p of m[1].split(",")) { const n = p.trim().replace(/^\.\.\./, "").split(/[\s=:]/).pop(); if (/^[A-Za-z_$][\w$]*$/.test(n)) add(n); }
  }
  for (const m of s.matchAll(/\bfor\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) add(m[1]);

  const GLOBALS = new Set(["console","document","window","globalThis","fetch","setTimeout","clearTimeout","setInterval","clearInterval","requestAnimationFrame","cancelAnimationFrame","localStorage","sessionStorage","location","navigator","history","WebSocket","Notification","URL","URLSearchParams","FormData","Blob","File","FileReader","Image","Date","Math","JSON","Object","Array","String","Number","Boolean","Promise","Set","Map","WeakMap","WeakSet","Symbol","Error","TypeError","RangeError","RegExp","Intl","encodeURIComponent","decodeURIComponent","encodeURI","decodeURI","parseInt","parseFloat","isNaN","isFinite","structuredClone","queueMicrotask","AbortController","Event","CustomEvent","MutationObserver","ResizeObserver","IntersectionObserver","performance","crypto","atob","btoa","alert","confirm","prompt","undefined","NaN","Infinity","this","arguments","super","import","new","typeof","instanceof","void","delete","in","of","await","yield","return","if","else","for","while","do","switch","case","default","break","continue","try","catch","finally","throw","function","class","extends","const","let","var","export","from","as","async","static","get","set","true","false","null","then"]);

  const KEYWORD_CALL = /(?<![.\w$?])([A-Za-z_$][\w$]*)\s*(?=[(.,;)\]}=+\-*/<>!&|?:\s]|$)/g;
  const free = new Map();
  for (const m of s.matchAll(KEYWORD_CALL)) {
    const n = m[1];
    if (declared.has(n) || GLOBALS.has(n)) continue;
    free.set(n, (free.get(n) || 0) + 1);
  }
  // Two shapes look like free references and are not. Both produced findings on real modules here:
  //
  //   { handle() {} }                      method shorthand — reads exactly like a call
  //   { renderRail, renderConversation }    object-literal keys — read as bare references
  //
  // The second is what a factory looks like when it assembles its return value, so without this a
  // module like chat.js reports a dozen names it never referenced. A name survives only if it is used
  // in call or member position AND is neither of those two shapes anywhere in the file.
  const confirmed = [...free.keys()].filter((n) => {
    if (new RegExp(`(?<![.\\w$])${n}\\s*\\([^)]*\\)\\s*\\{`).test(s)) return false;
    if (new RegExp(`[{,]\\s*${n}\\s*[,}:]`).test(s)) return false;
    return new RegExp(`(?<![.\\w$])${n}\\s*\\(`).test(s) || new RegExp(`(?<![.\\w$])${n}\\.`).test(s);
  });

  return confirmed.sort();
}

/**
 * Every name a module exports, keyed by filename.
 * @param {Record<string,string>} sources filename -> source text
 */
export function exportedNames(sources) {
  const byName = new Map();
  for (const [file, src] of Object.entries(sources)) {
    for (const m of src.matchAll(/^export\s+(?:async\s+)?(?:function\s*\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm)) {
      if (!byName.has(m[1])) byName.set(m[1], []);
      byName.get(m[1]).push(file);
    }
    for (const m of src.matchAll(/^export\s*\{([^}]*)\}/gm)) {
      for (let n of m[1].split(",")) {
        n = n.trim();
        if (!n) continue;
        const as = n.match(/\bas\s+(\S+)$/);
        const name = as ? as[1] : n;
        if (!byName.has(name)) byName.set(name, []);
        byName.get(name).push(file);
      }
    }
  }
  return byName;
}

/**
 * The finding that matters: names a module uses without importing, WHICH A SIBLING MODULE EXPORTS.
 *
 * `freeNames` alone is a heuristic over text and reports things that are not references — a word
 * ending a comment sentence, an attribute fragment like `data-thread-id`, the second declarator of a
 * multi-name `const`. Intersecting with the sibling export set removes all of that without an
 * allowlist to rot, because none of those words is exported by anything.
 *
 * It also sharpens the finding. Every one of the four names that shipped missing — `relTime`,
 * `runPendingControlCount`, `runTo`, `runRuntime` — was an export of a sibling (or a helper that had
 * to become one), which is what a relocation gets wrong: the code moved, the import did not follow.
 *
 * @returns {Array<{name: string, from: string[]}>}
 */
export function missingImports(src, exportIndex) {
  return freeNames(src)
    .filter((name) => exportIndex.has(name))
    .map((name) => ({ name, from: exportIndex.get(name) }));
}
