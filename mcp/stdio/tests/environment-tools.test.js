// The environment tools, executed rather than scanned.
//
// `comms_envs` lists the environments the hub knows about; `comms_spawn` starts a managed agent in one.
// They share one private helper, `summarizeEnvironment`, which renders an environment line for BOTH — the
// listing and the error a failed spawn returns. That shared rendering is the reason they are one group: if
// it lived in one module and one of its callers in another, the failure mode is two lists that drift, so a
// caller is told one thing by the listing and another by the error.
//
// Until v0.5.4 all of it lived in `server.js`, the bin entry point, which nothing imports.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const environmentModule = await import("../environment-tools.mjs");
const { registerEnvironmentTools } = environmentModule;
const { z } = await import("zod");

const tools = new Map();
registerEnvironmentTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;

test("the wrapper registers exactly the two environment tools", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_envs", "comms_spawn"]);
  for (const [name, tool] of tools) {
    assert.equal(typeof tool.handler, "function", `${name} must have a handler`);
    assert.ok(tool.description.length > 20, `${name} must describe itself`);
  }
});

test("comms_spawn's name guard sits BEHIND its remote-mode guard, so local mode cannot reach it", async () => {
  // THIS TEST REPLACES A VACUOUS ONE, and the way it was caught is the point. I asserted that
  // `comms_spawn` rejects traversal-shaped agent ids, passed four of them, and got `isError` every time —
  // so it looked proven. Then removing the `validateName` call entirely killed NOTHING: the `IS_REMOTE`
  // check runs first, so in local mode the handler returns before the name is ever examined. Every
  // rejection I was crediting to the guard came from the mode check.
  //
  // A mutation that changes nothing is the only reliable way to find this. Four passing assertions about
  // a code path that never executed would have read as coverage forever.
  const spawn = tools.get("comms_spawn");
  assert.ok(spawn.schema.agentId, "an agent id is required to spawn one");

  // What IS true from here: the ordering. Asserted on source because it is an ordering fact, and it is
  // what makes the guard untestable FROM THIS FILE — which needs local mode, and `IS_REMOTE` resolves
  // once per process.
  //
  // THE GAP THIS NOTE RECORDED IS CLOSED (2026-08-17): `environment-tools-remote.test.js` runs the same
  // handlers against a real service, where the mode guard passes and the name guard is the next thing to
  // execute. Removing the `validateName` call now fails a test there — checked by doing it.
  const src = readFileSync(path.join(STDIO, "environment-tools.mjs"), "utf-8");
  const spawnBody = src.slice(src.indexOf('"comms_spawn"'));
  const remoteAt = spawnBody.indexOf("if (!IS_REMOTE)");
  const validateAt = spawnBody.indexOf("validateName(agentId");
  assert.ok(remoteAt !== -1 && validateAt !== -1, "both guards must still exist");
  assert.ok(
    remoteAt < validateAt,
    "the mode guard runs first; if that ever inverts, the local-mode refusal stops being reachable",
  );

  // And a bad id in local mode yields the MODE refusal, not the name one — the distinction the vacuous
  // version could not see.
  const res = await spawn.handler({ agentId: "../escape", runtime: "claude" });
  assert.equal(res.isError, true);
  assert.match(
    text(res), /HTTP-backed aify-comms service|environment/i,
    "in local mode the refusal comes from the mode check, not from name validation",
  );
  assert.doesNotMatch(text(res), /Invalid agent ID/, "the name guard is not reached here");
});

test("in local mode both tools refuse rather than pretending to have an environment", async () => {
  // Environments are a service concept — a local filesystem store has none. A spawn that reported success
  // here would leave a caller waiting for a worker that was never started.
  for (const name of ["comms_envs", "comms_spawn"]) {
    const res = await tools.get(name).handler({ agentId: "agent-a", runtime: "claude" });
    assert.equal(res.isError, true, `${name} must report an error in local mode`);
    assert.ok(!/undefined|\[object Object\]/.test(text(res)), `${name} leaked a placeholder: ${text(res)}`);
  }
});

test("the shared renderer is PRIVATE, and is the only environment renderer in the group", () => {
  // The reviewer's rule for group leaves: export the owner surface, keep group-exclusive helpers inside.
  // Its output is asserted through the two tools rather than by importing it, and the property that
  // matters here is that there is exactly ONE renderer — a second would be the drift this group exists to
  // prevent.
  assert.deepEqual(Object.keys(environmentModule).sort(), ["registerEnvironmentTools"], "one export: the wrapper");

  const src = readFileSync(path.join(STDIO, "environment-tools.mjs"), "utf-8");
  assert.equal(
    (src.match(/function summarizeEnvironment\b/g) || []).length, 1,
    "exactly one definition of the shared renderer",
  );
  // Both callers must reach that one definition. Two hand-rolled renderings would satisfy a
  // "does it mention environments" test while disagreeing with each other.
  assert.ok(
    (src.match(/summarizeEnvironment/g) || []).length >= 3,
    "the renderer should have its definition plus both callers",
  );
});

test("server.js kept neither tool nor the helper — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["comms_envs", "comms_spawn"]) {
    assert.doesNotMatch(src, new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`), `${name} still in server.js`);
  }
  assert.doesNotMatch(src, /^(?:export\s+)?function\s+summarizeEnvironment\b/m, "the helper must not be redeclared");
  assert.doesNotMatch(src, /(?<![\w.])summarizeEnvironment(?![\w])/, "server.js has no remaining reference to it");
  // Moved with the registration list to `register-tools.mjs` in v0.5.4. Still a wiring check —
  // "the wrapper is called with exactly (server, z)" is about wiring, not behaviour — but it now
  // names the file that holds the call.
  const reg = readFileSync(path.join(STDIO, "register-tools.mjs"), "utf-8");
  assert.match(reg, /registerEnvironmentTools\(server, z\);/, "the registrar must still CALL the wrapper");
});

test("the module kept no state and imports only owned leaves", () => {
  const src = readFileSync(path.join(STDIO, "environment-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  // THE SCANNER HAD TO BE WIDENED TO SEE ITS OWN SUBJECT. `/^import .* from "..";$/m` matches only
  // SINGLE-LINE imports, so a braced multi-line one was invisible -- this module could have
  // imported server.js across three lines and the assertion below would have passed. The list is
  // the point of the test, and a list that cannot see half the syntax is not one.
  const IMPORT = /^import\s[\s\S]*?from\s+"([^"]+)";$/gm;
  const imports = [...src.matchAll(IMPORT)].map((m) => m[1]);
  assert.ok(imports.length >= 4, `the import scanner found ${imports.length}; it is not reading the file`);
  // `spawn-claimer.mjs` is a leaf and pure. The claim predicates began inside the doctor, where an
  // MCP tool group cannot follow them -- importing the doctor to answer a question that is not the
  // doctor's would drag its filesystem and home-directory reads into every agent's bridge.
  // Splitting them out is what let both instruments ask the same question.
  assert.deepEqual(
    imports.sort(),
    ["./aify-service-endpoint.mjs", "./runtimes.js", "./safe-name.mjs", "./spawn-claimer.mjs"],
    "it should reach only for owned leaves - no server.js, no zod, and no doctor-predicates.js",
  );
});
