// What `tools/list` costs, measured from the registrations rather than estimated.
//
// WHY IT IS MEASURED AT ALL. This payload is ALWAYS-LOADED context: every agent re-reads every tool
// description and every field description on every turn, for the life of the fleet. A sentence added
// here is not written once, it is paid continuously and by everyone -- which is the same argument
// the skill-size ratchet already makes for `SKILL.md`, on a surface nobody was watching.
//
// PARSED, NOT IMPORTED, and that is not a stylistic choice. Importing the registration modules to
// ask them for their strings would pull in `server.js` and its neighbours, and modules in this
// directory do work at import time -- one of them starts a daemon. A measurement that has to run the
// fleet to count it is not a measurement anybody will run.
//
// BOTH HALVES OR THE NUMBER LIES. A tool's cost is its description PLUS every `.describe()` on its
// schema fields; the schema half is invisible in the source (it is scattered one line per field) and
// on some tools it is the larger of the two. Counting descriptions alone would report a tool as
// cheap while its parameters carried most of the weight.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Walk forward from `open` (the index of a "(") to its matching ")", skipping string bodies. */
function spanOf(src, open) {
  let depth = 0;
  for (let i = open; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch;
      i += 1;
      while (i < src.length && src[i] !== quote) {
        if (src[i] === "\\") i += 1;
        i += 1;
      }
      continue;
    }
    if (ch === "(") depth += 1;
    else if (ch === ")") {
      depth -= 1;
      if (depth === 0) return { start: open, end: i };
    }
  }
  return null;
}

/** Total characters of every string literal in `text`, escapes resolved the cheap way. */
function literalChars(text) {
  let total = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch !== '"' && ch !== "'" && ch !== "`") continue;
    const quote = ch;
    i += 1;
    let n = 0;
    while (i < text.length && text[i] !== quote) {
      if (text[i] === "\\") i += 1;
      i += 1;
      n += 1;
    }
    total += n;
  }
  return total;
}

/** The `.describe("…")` strings inside a registration's argument span. */
function describeChars(args) {
  let total = 0;
  let at = 0;
  for (;;) {
    const found = args.indexOf(".describe(", at);
    if (found < 0) return total;
    const span = spanOf(args, found + ".describe".length);
    if (!span) return total;
    total += literalChars(args.slice(span.start, span.end + 1));
    at = span.end;
  }
}

/**
 * Every tool this bridge registers, with what it costs in `tools/list`.
 *
 * A description given as a CONSTANT is resolved from the same file, because two of them are exported
 * that way and a parser that only understood inline literals would report those tools as free --
 * which is the reading that would have let the biggest description in the tree escape the gate.
 *
 * @returns {{name: string, file: string, description: number, schema: number, total: number}[]}
 */
export function measureToolSurface() {
  const tools = [];
  for (const file of readdirSync(STDIO).filter((f) => f.endsWith(".mjs") || f.endsWith(".js"))) {
    const src = readFileSync(join(STDIO, file), "utf8");
    if (!src.includes("server.tool(")) continue;
    let at = 0;
    for (;;) {
      const found = src.indexOf("server.tool(", at);
      if (found < 0) break;
      const span = spanOf(src, found + "server.tool".length);
      if (!span) break;
      at = span.end;
      const args = src.slice(span.start + 1, span.end);
      const name = /^\s*"([a-z_]+)"/.exec(args)?.[1];
      if (!name) continue;
      // The description is everything between the name and the schema object that follows it.
      const afterName = args.indexOf(",", args.indexOf(name)) + 1;
      const schemaAt = args.indexOf("{", afterName);
      const descExpr = schemaAt < 0 ? "" : args.slice(afterName, schemaAt);
      let description = literalChars(descExpr);
      // A bare constant: resolve it from this file's own exports.
      const constName = /^\s*([A-Z][A-Z0-9_]*)\s*,/.exec(descExpr)?.[1];
      if (constName) {
        const decl = src.indexOf(`const ${constName} =`);
        if (decl >= 0) {
          const semi = src.indexOf(";\n", decl);
          description = literalChars(src.slice(decl, semi < 0 ? src.length : semi));
        }
      }
      const schema = describeChars(args);
      tools.push({ name, file, description, schema, total: description + schema });
    }
  }
  return tools.sort((a, b) => b.total - a.total);
}
