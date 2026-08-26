// A parameter an MCP tool DECLARES is a parameter something must READ.
//
// THE DEFECT CLASS IS PROVEN IN THIS REPO, twice, on the two surfaces beside this one.
// `managedClaudeMaxTurns` was exported from `runtimes.js` and called by nothing -- somebody looking
// for "how do I cap turns" finds it, sets it, and nothing happens. `dashboard_refresh_seconds` was a
// settings key hardcoded past, which is why `test_every_setting_has_a_reader.py` exists.
//
// A TOOL PARAMETER IS THE SAME DEFECT, AGENT-FACING AND WORSE. The schema IS the contract an agent
// reads: it passes the field, the call succeeds, and nothing happens. No error, no log, no way to
// learn it was ignored -- the agent concludes the SYSTEM is broken rather than the wiring, which is
// exactly what the settings gate's docstring says about knobs.
//
// WHAT THIS CANNOT TELL YOU, said plainly: it asks whether a name is MENTIONED in the code that
// handles the call, not whether reading it changes anything. A parameter destructured into a variable
// nobody uses would pass. That is the weaker question deliberately -- the stronger one needs the
// repo's reference resolver rather than a scan, and the case worth catching is the parameter with no
// mention at all.
//
// MEASURED 2026-08-26: 32 tools carry a zod shape, and every declared parameter has a reader. That
// zero is load-bearing, so the test below injects a parameter nothing could read and requires the
// scan to report it -- a probe that cannot return PRESENT cannot return ABSENT.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function bridgeSources() {
  return readdirSync(DIR)
    .filter((name) => /\.(js|mjs)$/.test(name) && !/\.test\./.test(name))
    .filter((name) => {
      try { return statSync(path.join(DIR, name)).isFile(); } catch { return false; }
    });
}

/** Balanced-brace slice starting at `from`, which must index the opening brace. */
function braced(text, from) {
  let depth = 0;
  for (let i = from; i < text.length; i += 1) {
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") { depth -= 1; if (depth === 0) return text.slice(from, i + 1); }
  }
  return "";
}

/**
 * Every `comms_*` tool in `source`, with its declared parameters and the code that handles it.
 *
 * `bodies` maps a module name to its text, so a handler declared elsewhere can be followed.
 */
export function toolsIn(source, bodies = new Map()) {
  const found = [];
  for (const match of source.matchAll(/\.tool\(\s*["'`](comms_[a-z_]+)["'`]/g)) {
    const toolName = match[1];
    // The zod shape is the first `{` after the description argument.
    const shapeAt = source.indexOf("{", source.indexOf(",", source.indexOf(",", match.index) + 1));
    if (shapeAt === -1) continue;
    const shape = braced(source, shapeAt);
    if (!shape.includes("z.")) continue;

    // KEYS BY CHARACTER DEPTH, not by line. A line-based walk returns NOTHING for a shape written on
    // one line -- which every real tool avoids and a fixture does not, so the first version of this
    // scan reported a single-line shape as having no parameters at all. That is the failure mode
    // where a gate passes because it saw nothing.
    const keys = [];
    let depth = 0;
    let token = "";
    for (let i = 0; i < shape.length; i += 1) {
      const ch = shape[i];
      if (ch === "{" || ch === "(" || ch === "[") { depth += 1; token = ""; continue; }
      if (ch === "}" || ch === ")" || ch === "]") { depth -= 1; token = ""; continue; }
      if (ch === ",") { token = ""; continue; }
      if (ch === ":" && depth === 1) {
        const name = token.trim();
        // The VALUE must be a zod expression, or this is an object literal nested in a describe()
        // rather than a parameter. `z.` immediately after the colon is what distinguishes them.
        const after = shape.slice(i + 1, i + 40);
        if (/^[A-Za-z_$][\w$]*$/.test(name) && /^\s*z\./.test(after)) keys.push(name);
        token = "";
        continue;
      }
      token += ch;
    }

    // MOST TOOLS FORWARD WHOLESALE: `(args) => commsConsoleTailHandler(args)`. Asking whether that
    // arrow mentions `lines` asks the wrong function, and reported three false findings on this
    // scan's first run. Follow the named handler to wherever it is declared.
    const from = shapeAt + shape.length;
    let handler = source.slice(from, from + 6000);
    const forwarded = /\(\s*args\s*\)\s*=>\s*([A-Za-z_$][\w$]*)\s*\(/.exec(handler)
      || /^\s*,\s*([A-Za-z_$][\w$]*)\s*\)/.exec(handler);
    if (forwarded) {
      const fn = forwarded[1];
      for (const text of bodies.values()) {
        for (const decl of [`function ${fn}(`, `const ${fn} = `, `async function ${fn}(`]) {
          const at = text.indexOf(decl);
          if (at === -1) continue;
          const open = text.indexOf("{", at);
          if (open !== -1) handler += String.fromCharCode(10) + braced(text, open);
        }
      }
    }
    const unread = keys.filter((key) => !new RegExp(`\\b${key}\\b`).test(handler));
    found.push({ toolName, keys, unread });
  }
  return found;
}

function scanBridge() {
  const bodies = new Map();
  for (const name of bridgeSources()) bodies.set(name, readFileSync(path.join(DIR, name), "utf8"));
  const tools = [];
  for (const [name, source] of bodies) {
    for (const tool of toolsIn(source, bodies)) tools.push({ ...tool, module: name });
  }
  return tools;
}

test("the scan reads a real population of tools", () => {
  // ANTI-VACUITY. Zero tools parsed reports a clean bridge, which is how a broken scan looks.
  const tools = scanBridge();
  assert.ok(tools.length > 20, `only ${tools.length} tools with a zod shape were found`);
  assert.ok(
    tools.some((t) => t.toolName === "comms_console_tail"),
    "a tool known to exist was not parsed",
  );
});

test("the scan can say PRESENT", () => {
  // A parameter nothing could possibly read, in an otherwise ordinary registration. Without this the
  // assertion below is a zero from an instrument nobody has watched work.
  const fixture = [
    'server.tool(',
    '  "comms_probe",',
    '  "a probe",',
    '  {',
    '    agentId: z.string().describe("who"),',
    '    zzNoReaderZz: z.string().optional().describe("nothing reads this"),',
    '  },',
    '  (args) => probeHandler(args)',
    ');',
    'function probeHandler({ agentId }) { return agentId; }',
  ].join("\n");
  const [tool] = toolsIn(fixture, new Map([["probe.mjs", fixture]]));
  assert.deepEqual(tool.unread, ["zzNoReaderZz"]);
});

test("the scan can say ABSENT", () => {
  // The same fixture with the parameter read. Both controls, in the same run as the zero they defend.
  const fixture = [
    'server.tool(',
    '  "comms_probe",',
    '  "a probe",',
    '  {',
    '    agentId: z.string().describe("who"),',
    '    lines: z.number().optional().describe("how many"),',
    '  },',
    '  (args) => probeHandler(args)',
    ');',
    'function probeHandler({ agentId, lines }) { return [agentId, lines]; }',
  ].join("\n");
  const [tool] = toolsIn(fixture, new Map([["probe.mjs", fixture]]));
  assert.deepEqual(tool.unread, []);
});

test("a handler that FORWARDS its arguments is followed to where it is declared", () => {
  // The mistake this scan made first: `(args) => handler(args)` mentions no parameter at all, so
  // every tool in the bridge looked broken. Three tools were reported before the follow existed.
  const registration = [
    'server.tool("comms_probe", "d", { lines: z.number() }, (args) => elsewhereHandler(args));',
  ].join("\n");
  const other = "export function elsewhereHandler({ lines }) { return lines; }";
  const [withFollow] = toolsIn(registration, new Map([["a.mjs", registration], ["b.mjs", other]]));
  assert.deepEqual(withFollow.unread, [], "the named handler was not followed");
  const [withoutFollow] = toolsIn(registration, new Map([["a.mjs", registration]]));
  assert.deepEqual(withoutFollow.unread, ["lines"], "the fixture does not exercise the follow at all");
});

test("every declared tool parameter has a reader", () => {
  const offenders = scanBridge()
    .filter((tool) => tool.unread.length)
    .map((tool) => `${tool.toolName} (${tool.module}): ${tool.unread.join(", ")}`);
  assert.deepEqual(offenders, [], (
    "these MCP tools declare a parameter nothing in their handler reads:\n  "
    + offenders.join("\n  ")
    + "\nAn agent reads the schema, passes the field, and the call succeeds with nothing done — no "
    + "error and no log, so it concludes the system is broken rather than the wiring. Wire it, or "
    + "remove it from the schema."
  ));
});
