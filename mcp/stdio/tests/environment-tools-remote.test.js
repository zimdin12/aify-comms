// `comms_envs` and `comms_spawn` in REMOTE mode — the only normal path to a new managed agent.
//
// SEPARATE FROM `environment-tools.test.js` BY NECESSITY, not by preference. `IS_REMOTE` is derived from
// the server URL at module load, once per process, and that sibling deliberately runs in LOCAL mode: it
// proves the two tools refuse rather than pretending to have an environment, and it records — from a
// mutation that killed nothing — that `comms_spawn`'s `validateName` call sits BEHIND the `IS_REMOTE`
// guard, so in local mode the name check is never reached. Its note says so and calls the gap visible.
//
// THIS FILE CLOSES THAT GAP by running the same handlers against a real service, where the mode guard
// passes and the name guard is the next thing to execute. Same tools, opposite mode, one process each.
//
// Third cluster off the V8-coverage census: neither handler nor the renderer they share had ever been
// CALLED by a test. `summarizeEnvironment` is module-private, and the module's docstring says its output
// is "asserted through them" — which was true of the intention and not of the suite.
//
// WHY THE SHARED RENDERER MATTERS. `comms_spawn` cannot be used without knowing which environments
// exist, so when a spawn fails for want of one it renders the available environments with the SAME
// helper `comms_envs` uses. If those two ever diverged, an agent would be told one thing by the listing
// and another by the failure it is trying to recover from — so the test compares the two renderings
// BYTE FOR BYTE rather than checking each against a copy of the expected text.
//
// WHAT SPAWN REFUSES IS THE REST OF IT. Four distinct refusals, each with its own message, and the
// distinctions are what an agent acts on: a bad agent id is the caller's mistake, a missing environment
// is a choice to make, an OFFLINE environment needs a bridge started, and one that does not advertise
// the runtime needs a different host. Collapsing any pair would send an agent to fix the wrong thing.
//
// A REAL HTTP SERVICE on 127.0.0.2, set before the import: `IS_REMOTE` is derived from the server URL at
// module load, once per process, and both handlers refuse outright without it.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

const REQUESTS = [];
let REPLY = { environments: [] };

const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(typeof REPLY === "function" ? REPLY(req) : REPLY));
  });
});
const PORT = await new Promise((resolve) => {
  SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port));
});
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a
// live wrapper environment exports it. Setting only the new name left the fake below unused.
process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
const { registerEnvironmentTools } = await import("../environment-tools.mjs");

test.after(() => SERVER.close());

function fakeZod() {
  const spec = (kind) => {
    const self = {
      kind,
      optional() { self.isOptional = true; return self; },
      describe() { return self; },
    };
    return self;
  };
  return { string: () => spec("string"), enum: (values) => spec(`enum:${values.join("|")}`) };
}

function tools() {
  const registered = [];
  registerEnvironmentTools({
    tool(name, description, schema, callback) {
      registered.push({ name, description, schema, callback });
    },
  }, fakeZod());
  return registered;
}

function tool(name) {
  const found = tools().find((t) => t.name === name);
  assert.ok(found, `${name} was not registered`);
  return found;
}

function text(result) {
  return result.content.map((c) => c.text).join("\n");
}

const ONLINE = {
  id: "env-wsl",
  status: "online",
  label: "WSL Ubuntu",
  os: "linux",
  kind: "wsl",
  runtimes: [{ runtime: "claude-code" }, { runtime: "codex" }],
  cwdRoots: ["/home/dev", "/srv"],
};

function reset(reply) {
  REQUESTS.length = 0;
  REPLY = reply;
}

// ── registration ────────────────────────────────────────────────────────────────────────────────

test("exactly the two environment tools are registered", () => {
  assert.deepEqual(tools().map((t) => t.name), ["comms_envs", "comms_spawn"]);
});

test("spawn REQUIRES the fields a spawn cannot be inferred from", () => {
  // A spawn with no owner, id, role or runtime has nothing to create. Making any of them optional
  // moves the failure from the schema — where a model sees it — to the service.
  const schema = tool("comms_spawn").schema;
  for (const field of ["from", "agentId", "role", "runtime"]) {
    assert.ok(!schema[field].isOptional, `${field} is optional`);
  }
  for (const field of ["environmentId", "workspace", "name", "model", "instructions",
    "initialMessage", "subject", "priority"]) {
    assert.ok(schema[field].isOptional, `${field} is required`);
  }
});

test("PRIORITY is an enum, not free text", () => {
  // The service has three priorities. A free string would let a model invent a fourth that silently
  // becomes "normal" one layer down.
  assert.equal(tool("comms_spawn").schema.priority.kind, "enum:normal|high|urgent");
});

// ── listing ─────────────────────────────────────────────────────────────────────────────────────

test("an environment is rendered with its status, host and roots", () => {
  // What a model reads to choose a host. The status and the roots are the two facts a spawn depends
  // on, and a line that omitted either would make the next call a guess.
  reset({ environments: [ONLINE] });
  return tool("comms_envs").callback({}).then((result) => {
    const rendered = text(result);
    assert.match(rendered, /1 environment\(s\)/);
    assert.match(rendered, /env-wsl \[online\] WSL Ubuntu/);
    assert.match(rendered, /linux\/wsl/);
    assert.match(rendered, /runtimes: claude-code, codex/);
    assert.match(rendered, /roots: \/home\/dev, \/srv/);
  });
});

test("MISSING fields render as 'unknown' rather than as blanks", () => {
  // A half-registered environment is a real state, and an operator reading `- env-x [] ` cannot tell
  // an empty status from a missing one.
  reset({ environments: [{ id: "env-bare" }] });
  return tool("comms_envs").callback({}).then((result) => {
    const rendered = text(result);
    assert.match(rendered, /env-bare \[unknown\]/);
    assert.match(rendered, /unknown\/unknown/);
    assert.match(rendered, /no runtimes/);
    assert.match(rendered, /no roots/);
  });
});

test("NO environments says what to start, not just that there are none", () => {
  reset({ environments: [] });
  return tool("comms_envs").callback({}).then((result) => {
    assert.match(text(result), /Start `aify-comms` in WSL\/Linux/);
  });
});

// ── the shared renderer ─────────────────────────────────────────────────────────────────────────

test("a FAILED SPAWN renders the environments IDENTICALLY to the listing", async () => {
  // The invariant the module was built around, compared byte for byte. Two copies of this rendering
  // would drift, and an agent recovering from a spawn failure would be reading the stale one.
  reset({ environments: [ONLINE] });
  const listed = text(await tool("comms_envs").callback({}));
  const listedLines = listed.split("\n").slice(1).join("\n");

  reset({ environments: [ONLINE] });
  const failed = text(await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "pi",
  }));
  const failedLines = failed.split("Available environments:\n")[1];

  assert.equal(failedLines, listedLines);
});

// ── spawn refusals ──────────────────────────────────────────────────────────────────────────────

test("an INVALID agent id is refused before anything is created", async () => {
  // The caller's own mistake, and the one refusal that must not reach the service: a spawn request
  // written under a traversing or malformed id is a row nothing can address afterwards.
  reset({ environments: [ONLINE] });
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "../evil", role: "coder", runtime: "claude-code",
  });
  assert.equal(result.isError, true);
  assert.equal(REQUESTS.length, 0, "a bad agent id still reached the service");
});

test("NO MATCHING environment is refused with the runtime NAMED", async () => {
  reset({ environments: [ONLINE] });
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "pi",
  });
  assert.equal(result.isError, true);
  assert.match(text(result), /No matching environment found for runtime "pi"/);
});

test("an OFFLINE environment named explicitly is refused with its STATUS", async () => {
  // A different action from "no environment matched": this one exists and its bridge is down.
  reset({ environments: [{ ...ONLINE, status: "offline" }] });
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
    environmentId: "env-wsl",
  });
  assert.equal(result.isError, true);
  assert.match(text(result), /is offline, not online\. Start its bridge first\./);
});

test("an environment that does NOT ADVERTISE the runtime is refused as such", async () => {
  // The third distinct action: the host is up, and the runtime is not installed on it.
  reset({ environments: [{ ...ONLINE, runtimes: [{ runtime: "codex" }] }] });
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
    environmentId: "env-wsl",
  });
  assert.equal(result.isError, true);
  assert.match(text(result), /does not advertise runtime "claude-code"/);
});

test("NO environment at all says so instead of listing nothing", async () => {
  reset({ environments: [] });
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  assert.match(text(result), /No environment bridges are connected\./);
});

// ── spawn selection ─────────────────────────────────────────────────────────────────────────────

test("with no environmentId the first ONLINE host advertising the runtime is chosen", async () => {
  // Both conditions, and both matter: an offline host that advertises the runtime cannot start it, and
  // an online host that does not have it cannot either.
  reset({
    environments: [
      { ...ONLINE, id: "env-offline", status: "offline" },
      { ...ONLINE, id: "env-wrong-runtime", runtimes: [{ runtime: "codex" }] },
      { ...ONLINE, id: "env-right" },
    ],
  });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  const posted = REQUESTS.filter((r) => r.method === "POST");
  assert.equal(JSON.parse(posted[0].body).environmentId, "env-right");
});

test("the RUNTIME is normalised before it is matched and before it is sent", async () => {
  // `claude` and `claude-code` are the same runtime, and the environment advertises one spelling. An
  // unnormalised match refuses a host that can in fact run the agent.
  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude",
  });
  const posted = REQUESTS.filter((r) => r.method === "POST");
  assert.equal(JSON.parse(posted[0].body).runtime, "claude-code");
});

test("the WORKSPACE defaults to the environment's FIRST advertised root", async () => {
  // Spawning into an unadvertised path is what the environment roots exist to prevent, and a blank
  // workspace lands the agent wherever the bridge happens to be.
  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  const posted = REQUESTS.filter((r) => r.method === "POST");
  assert.equal(JSON.parse(posted[0].body).workspace, "/home/dev");
});

test("an explicit workspace WINS over the default", async () => {
  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
    workspace: "/srv/project",
  });
  const posted = REQUESTS.filter((r) => r.method === "POST");
  assert.equal(JSON.parse(posted[0].body).workspace, "/srv/project");
});

test("the spawn is created MANAGED-WARM with native-first resume", async () => {
  // The two policy fields the tool does not expose. They are what makes this "the only normal
  // agent-spawn path" rather than one shape among several a caller picks from.
  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  const body = JSON.parse(REQUESTS.filter((r) => r.method === "POST")[0].body);
  assert.equal(body.mode, "managed-warm");
  assert.equal(body.resumePolicy, "native_first");
  assert.equal(body.createdBy, "manager");
});

test("a SUBJECT is derived only when there is a brief to deliver", async () => {
  // A spawn with no initial message has nothing to be the subject OF, and inventing one would put an
  // empty task in the agent's inbox.
  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  assert.equal(JSON.parse(REQUESTS.filter((r) => r.method === "POST")[0].body).subject, "");

  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
    initialMessage: "rebuild the index",
  });
  assert.equal(JSON.parse(REQUESTS.filter((r) => r.method === "POST")[0].body).subject,
    "Brief new-agent");
});

test("an explicit subject is not overwritten by the derived one", async () => {
  reset({ environments: [ONLINE] });
  await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
    initialMessage: "rebuild the index", subject: "index work",
  });
  assert.equal(JSON.parse(REQUESTS.filter((r) => r.method === "POST")[0].body).subject, "index work");
});

test("the reply names the queued request and its status", async () => {
  // The caller polls on this id. A confirmation without it is a spawn the agent cannot follow up on.
  reset((req) => (req.method === "POST"
    ? { spawnRequest: { id: "sr-77", status: "queued" } }
    : { environments: [ONLINE] }));
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  assert.match(text(result), /Spawn request: sr-77 \[queued\]/);
  assert.match(text(result), /Queued persistent agent "new-agent" in env-wsl \(claude-code, \/home\/dev\)/);
});

test("a service that returns NO spawn request still reports something followable", async () => {
  reset((req) => (req.method === "POST" ? {} : { environments: [ONLINE] }));
  const result = await tool("comms_spawn").callback({
    from: "manager", agentId: "new-agent", role: "coder", runtime: "claude-code",
  });
  assert.match(text(result), /Spawn request: unknown \[queued\]/);
});
