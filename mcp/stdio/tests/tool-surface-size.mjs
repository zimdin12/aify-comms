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
//
// A LOST REGISTRATION IS THE FAILURE MODE, TWICE OVER SO FAR. Both times the scanner mistook
// something for a string, ran past a registration's closing paren, and reported fewer tools than
// exist -- and a ratchet cannot hold a ceiling over a tool it cannot see. `tool-surface-ratchet`
// therefore counts `server.tool(` in the tree and demands a measurement for every one, which is the
// control that fires when this instrument breaks rather than when the code does.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * Characters after which a `/` opens a REGULAR EXPRESSION rather than dividing.
 *
 * The slash alone cannot say which it is -- JavaScript itself decides from the token before it --
 * and that ambiguity is why this is a list rather than a rule. `""` covers the start of the text.
 */
const BEFORE_A_REGEX = new Set(
  ["", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "~", "^", "<", ">"],
);

/**
 * The other half: a keyword may precede a regex where a bare identifier may not.
 *
 * IT READS RAW CHARACTERS, so a comment ending in one of these words counts as the word. Left as is
 * deliberately: it can only make the parser MORE willing to see a regex, and an over-eager regex is
 * measurably harmless here -- forcing every slash to open one loses no registration, because both
 * readings skip the same characters and `regexEnd` refuses to cross a newline. Missing a regex is
 * the direction that costs tools, and this cannot cause that.
 */
const KEYWORD_BEFORE_A_REGEX =
  /\b(return|typeof|instanceof|in|of|new|delete|void|throw|case|do|else|yield|await)$/;

/** Whether the `/` at `i` opens a regex, decided from the last significant token before it. */
function startsARegex(src, i, prev) {
  if (BEFORE_A_REGEX.has(prev)) return true;
  return KEYWORD_BEFORE_A_REGEX.test(src.slice(Math.max(0, i - 12), i).trimEnd());
}

/**
 * The index just past the regex literal starting at `i`, or -1 if what starts there is not one.
 *
 * IT REFUSES TO CROSS A NEWLINE, which is a fail-safe rather than a nicety. A regex literal cannot
 * contain a raw newline, so reaching one means the guess above misfired on a division -- and bailing
 * costs one misread slash, where continuing would swallow the rest of the file.
 */
function regexEnd(src, i) {
  let inClass = false;
  let j = i + 1;
  for (; j < src.length; j += 1) {
    const ch = src[j];
    if (ch === "\\") { j += 1; continue; }
    if (ch === "\n") return -1;
    if (ch === "[") inClass = true;
    else if (ch === "]") inClass = false;
    else if (ch === "/" && !inClass) break;
  }
  if (j >= src.length) return -1;
  j += 1;
  while (j < src.length && src[j] >= "a" && src[j] <= "z") j += 1;
  return j;
}

/**
 * The template literal starting at `i`, whose `${…}` holes contain CODE and not text.
 *
 * A BACKTICK-TO-BACKTICK SCAN IS WRONG WHENEVER A HOLE HOLDS ANOTHER TEMPLATE, which
 * `dashboard-tool.mjs` does five times in one HTML builder: the outer template ends at the first
 * INNER backtick, and everything after it is read in the wrong phase. That file balanced anyway for
 * a while, by luck -- two later misreadings cancelled -- and fixing the regex handling changed the
 * luck and lost `comms_dashboard`. Luck is not a property to preserve, so the holes are parsed.
 *
 * `chars` counts the hole's source text, exactly as the old scan did, because no tool description
 * interpolates and changing it would move every ceiling for no reason.
 */
function templateEnd(src, i) {
  let j = i + 1;
  let chars = 0;
  while (j < src.length && src[j] !== "`") {
    if (src[j] === "\\") { j += 2; chars += 1; continue; }
    if (src[j] === "$" && src[j + 1] === "{") {
      const close = interpolationEnd(src, j + 1);
      if (close < 0) break;
      chars += close + 1 - j;
      j = close + 1;
      continue;
    }
    j += 1;
    chars += 1;
  }
  return { end: j + 1, kind: "string", chars };
}

/** The index of the `}` closing the `${` whose brace is at `open`, or -1. */
function interpolationEnd(src, open) {
  let depth = 0;
  let prev = "";
  for (let i = open; i < src.length; i += 1) {
    const skipped = skipNonCode(src, i, prev);
    if (skipped) {
      if (skipped.kind !== "comment") prev = "x";
      i = skipped.end - 1;
      continue;
    }
    const ch = src[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
    if (ch !== " " && ch !== "\t" && ch !== "\n" && ch !== "\r") prev = ch;
  }
  return -1;
}

/**
 * What non-code begins at `i` -- a string literal, a comment, or a regex literal -- or null if code
 * does. `chars` is the payload length, which only a string has.
 *
 * COMMENTS ARE SKIPPED, NOT JUST STRINGS, and that was a real defect rather than a precaution.
 * Adding a code comment containing a double quote made `comms_compact` VANISH from the measurement:
 * the scanner read the comment's quote as a string opening and ran past the registration's closing
 * paren.
 *
 * REGEX LITERALS ARE SKIPPED FOR THE SAME REASON, found 2026-09-03 and named in this file's own
 * header as a limitation nothing in the tree had yet hit. Something had. `channel-tools.mjs` holds a
 * replace whose pattern is three backticks, and those backticks read as template literals -- one of
 * them ran two lines on and ate the closing paren of the `.replace(`. THREE registrations were lost
 * at once, every one after it in the file, and the ratchet passed all three because a ceiling is
 * only demanded of a tool the parser can see. The predicted shape was a quote inside a regex; the
 * live one was backticks, which is the argument for skipping the CONSTRUCT rather than the
 * character.
 *
 * `prev` is the last significant code character before `i` -- the only thing separating a regex from
 * a division.
 *
 * @returns {{end: number, kind: "string"|"comment"|"regex", chars: number}|null}
 */
function skipNonCode(src, i, prev = "") {
  const ch = src[i];
  if (ch === '"' || ch === "'") {
    let j = i + 1;
    let chars = 0;
    while (j < src.length && src[j] !== ch) {
      if (src[j] === "\\") j += 1;
      j += 1;
      chars += 1;
    }
    return { end: j + 1, kind: "string", chars };
  }
  if (ch === "`") return templateEnd(src, i);
  if (ch === "/" && src[i + 1] === "/") {
    const nl = src.indexOf("\n", i);
    return { end: nl < 0 ? src.length : nl, kind: "comment", chars: 0 };
  }
  if (ch === "/" && src[i + 1] === "*") {
    const end = src.indexOf("*/", i + 2);
    return { end: end < 0 ? src.length : end + 2, kind: "comment", chars: 0 };
  }
  if (ch === "/" && startsARegex(src, i, prev)) {
    const end = regexEnd(src, i);
    if (end > 0) return { end, kind: "regex", chars: 0 };
  }
  return null;
}

/**
 * Walk `text` from `at`, reporting each skipped construct to `onSkip` and each code character to
 * `onCode` (which stops the walk by returning false).
 *
 * ONE WALKER, because the two callers below had already drifted apart once: the span finder learned
 * to skip comments and the character counter did not, so the same construct was code to one and
 * payload to the other. They now see the text the same way by construction.
 */
function walkCode(text, at, onSkip, onCode) {
  let prev = "";
  for (let i = at; i < text.length; i += 1) {
    const skipped = skipNonCode(text, i, prev);
    if (skipped) {
      onSkip(skipped);
      // A COMMENT IS NOT A TOKEN, so it must not decide whether the next slash divides.
      if (skipped.kind !== "comment") prev = "x";
      i = skipped.end - 1;
      continue;
    }
    const ch = text[i];
    if (onCode && onCode(ch, i) === false) return;
    if (ch !== " " && ch !== "\t" && ch !== "\n" && ch !== "\r") prev = ch;
  }
}

/** Walk forward from `open` (the index of a "(") to its matching ")", skipping every non-code run. */
function spanOf(src, open) {
  let depth = 0;
  let end = -1;
  walkCode(src, open, () => {}, (ch, i) => {
    if (ch === "(") depth += 1;
    else if (ch === ")") {
      depth -= 1;
      if (depth === 0) { end = i; return false; }
    }
    return true;
  });
  return end < 0 ? null : { start: open, end };
}

/**
 * Total characters of every string literal in `text`, escapes resolved the cheap way.
 *
 * A COMMENT IS NOT PAYLOAD, and neither is a regex. An agent never receives either, so charging them
 * to a tool's budget would punish explaining the tool.
 */
function literalChars(text) {
  let total = 0;
  walkCode(text, 0, (skipped) => { if (skipped.kind === "string") total += skipped.chars; });
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

/** The files that register tools. */
function toolFiles() {
  return readdirSync(STDIO)
    .filter((f) => f.endsWith(".mjs") || f.endsWith(".js"))
    .filter((f) => readFileSync(join(STDIO, f), "utf8").includes("server.tool("));
}

/**
 * Every `server.tool(` WRITTEN in the tree, whether or not the parser can measure it.
 *
 * The control for `measureToolSurface`. Counting text is a weaker instrument than parsing, and that
 * is the point: the two fail differently, so the pair says whether the parser lost something.
 */
export function countToolRegistrations() {
  let total = 0;
  for (const file of toolFiles()) {
    total += readFileSync(join(STDIO, file), "utf8").split("server.tool(").length - 1;
  }
  return total;
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
  for (const file of toolFiles()) {
    for (const tool of registrationsIn(readFileSync(join(STDIO, file), "utf8"))) {
      tools.push({ ...tool, file });
    }
  }
  return tools.sort((a, b) => b.total - a.total);
}

/**
 * Every tool registered in one module's source, with what each costs.
 *
 * SEPARATE FROM THE FILE WALK so the parser can be proven on source it is HANDED. Its three hard
 * cases -- a division that is not a regex, a regex that is not a string, a template hole that is not
 * text -- do not all occur in the tree at once, and a parser only ever exercised on the current tree
 * is proven against today's code rather than against JavaScript.
 *
 * @returns {{name: string, description: number, schema: number, total: number}[]}
 */
export function registrationsIn(src) {
  const tools = [];
  let at = 0;
  for (;;) {
    const found = src.indexOf("server.tool(", at);
    if (found < 0) break;
    const span = spanOf(src, found + "server.tool".length);
    // A REGISTRATION WHOSE SPAN NEVER CLOSES IS SKIPPED, NOT A REASON TO STOP. Breaking here is
    // what turned one misparse into three lost tools: every later registration in the file went
    // with it. The count gate catches either shape, but losing one is a smaller lie than losing
    // the rest of the file.
    if (!span) { at = found + "server.tool(".length; continue; }
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
    tools.push({ name, description, schema, total: description + schema });
  }
  return tools;
}
