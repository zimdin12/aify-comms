// Read the bridge's source, all of it, without naming a file.
//
// WHY THIS EXISTS. Seven times during the v0.5.4 decomposition an assertion of mine hardcoded
// `../server.js` and went red on a slice that changed no behaviour — the code it was about had simply
// moved to a new module. Twice the assertion was younger than the commit that broke it. Each time the fix
// was the same: the property holds across the bridge, not in one file, so read the bridge.
//
// The failure is worse in the other direction. An `assert.doesNotMatch(serverSource, …)` keeps PASSING
// after its subject moves, because the subject is no longer in the file being scanned. A presence
// assertion fails loudly; an absence assertion goes quietly green and guards nothing. Anything asserting
// that something is ABSENT from the bridge must scan the whole bridge or it is decoration.
//
// Fixing the class rather than each instance, which is this repo's rule for anything a process can
// reproduce.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const STDIO_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Every JS/MJS module the bridge ships, as [filename, source] pairs.
//
// Excludes `tests/` (a different subdirectory, so not matched here anyway) and `fixtures/` — the
// pre-extraction snapshots there contain the very code a slice removed, so including them would let an
// absence assertion pass off a copy of the OLD file. That exclusion is load-bearing and is the reason this
// takes filenames rather than globbing everything.
export function bridgeSources() {
  return readdirSync(STDIO_DIR, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(js|mjs)$/.test(entry.name))
    .map((entry) => [entry.name, readFileSync(path.join(STDIO_DIR, entry.name), "utf-8")]);
}

// All of it concatenated, for "does this appear anywhere / nowhere" questions.
export function bridgeSource() {
  return bridgeSources().map(([, src]) => src).join("\n");
}

// The subset that registers MCP tools. The tool inventory is spread across group modules since v0.5.4, and
// a hardcoded list of them would have to be edited by whoever performs the next extraction — which is
// exactly when a tool goes missing from an inventory unnoticed.
export function toolSources() {
  return bridgeSources().filter(([, src]) => /server\.tool\(/.test(src));
}

// Which module DECLARES a name, as a `{file, kind}` or null. Use this instead of asserting a declaration
// lives in a particular file: it answers "exactly one owner" without caring which module that is.
export function declaringModules(name) {
  const patterns = [
    ["function", new RegExp(`^(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\b`, "m")],
    ["binding", new RegExp(`^(?:export\\s+)?(?:const|let|var)\\s+${name}\\b`, "m")],
  ];
  return bridgeSources().flatMap(([file, src]) =>
    patterns.filter(([, re]) => re.test(src)).map(([kind]) => ({ file, kind })),
  );
}

// Does anything in the bridge USE this name — a call, or a read that is not its own declaration?
export function isUsedInBridge(name) {
  const use = new RegExp(`(?<![\\w.])${name}(?![\\w])`);
  return bridgeSources().some(([, src]) => {
    const withoutDeclarations = src
      .replace(new RegExp(`^(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\b.*$`, "gm"), "")
      .replace(new RegExp(`^(?:export\\s+)?(?:const|let|var)\\s+${name}\\b.*$`, "gm"), "");
    return use.test(withoutDeclarations);
  });
}
