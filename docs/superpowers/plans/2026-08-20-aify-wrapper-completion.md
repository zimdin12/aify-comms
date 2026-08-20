# v0.6 Phase 6 — aify-wrapper completion: implementation plan

> **For agentic workers:** implement task-by-task. Each task ends with an independently testable
> deliverable and a commit. Steps use `- [ ]` for tracking.

**Goal.** Make aify-wrapper the single source of the launchers: it detects the harnesses present and
installs one launcher for each, it learns about services from a shared registry instead of a single
baked URL, and aify-comms consumes it rather than copying it.

**Architecture.** The registry `~/.aify/services.json` is written by each service's installer and read
by aify-wrapper **at install time**, never at launch. A launcher bakes the resolved endpoints plus a
fingerprint of the registry it was built from, so `--check` can report itself stale the same way
`HARNESS_WRAPPER_VERSION` already lets doctor report a wrapper out of date. Reading at launch was
rejected: hermes' MCP discovery window is 0.75s and this project has already lost that fight once.

**Tech stack.** Bash launchers rendered from `wrappers/*.sh.in`, a Node module for registry parsing
and its tests (`node --test`), no new dependencies.

## Global constraints

Every task's requirements implicitly include these. They are not style preferences; each one is a
recorded failure.

- **Never run a bare `aify-comms`.** It starts the environment bridge and reaps the managed fleet. Use
  `aify-comms --check`.
- **Never execute a wrapper to ask it something.** Read the marker out of the file. Asking a
  pre-contract wrapper `--check` forwards the flag to the runtime and launches Claude.
- **Never convert the hermes wrapper guards into executing tests on a host with live agents.** Isolated
  carrier only.
- **Hostile-env tests point NOWHERE (`http://127.0.0.2:1`), never at the live service.** A suite once
  registered six agents into the production registry.
- **Never set ACTION env vars in a test run** (`AIFY_ENVIRONMENT_BRIDGE`). Config vars are safe to set
  adversarially; role flags are not.
- **Seal every ambient input in a test:** env, `TMP`/`TEMP`, `XDG_STATE_HOME`, `HOME`. Assert the seal
  each call, and run the suite under a hostile env before claiming green.
- **`--render-only` must keep exiting before any env mutation, npm work or MCP registration.** That
  property is what lets the suite render and run real launchers on a machine with a live fleet.
- **Run `install.sh` sequentially, never in parallel.**
- **No secrets in commits.** Redact any key or connection string in output; report key presence and
  env-name only.
- Templates stay byte-identical across both repos until Task 6 deletes the reason.

## File structure

| Path | Repo | Responsibility |
|---|---|---|
| `lib/registry.mjs` | aify-wrapper | **New.** Pure: parse, validate, resolve endpoints, flatten MCP entries, fingerprint. No I/O. |
| `tests/registry.test.js` | aify-wrapper | **New.** Unit tests for the above, including malformed input. |
| `lib/detect-harnesses.mjs` | aify-wrapper | **New.** Pure: given a PATH-lookup function, return which harnesses are present. |
| `tests/detect-harnesses.test.js` | aify-wrapper | **New.** |
| `install.sh` | aify-wrapper | Modified: `--all`, registry read, fingerprint baking. |
| `wrappers/*.sh.in` | both | Modified: bake `HARNESS_REGISTRY_FINGERPRINT`; strict mode emits N entries. |
| `install.sh` | aify-comms | Modified: write its own registry entry; consume the package. |
| `service/tests/test_wrapper_templates_are_published_in_sync.py` | aify-comms | **Deleted in Task 6.** |

Pure logic goes in `lib/*.mjs` and is imported, following the repo's own rule that logic reachable
only through a shell script can only fail in production. `doctor-predicates.js` is the precedent, and
the first thing its extraction caught was a real bug.

---

### Task 1: The registry contract

**Files:** Create `lib/registry.mjs`, `tests/registry.test.js`. Create `docs/REGISTRY.md`.

**Interfaces — Produces:**
- `parseRegistry(text: string) -> {ok, registry?, errors[]}`
- `endpointFor(registry, serviceName) -> string | null`
- `mcpEntriesFor(registry) -> [{name, command, args, env}]`
- `fingerprint(registry) -> string` (stable across key order)

The schema. `endpointEnv` is the load-bearing field and exists because of a measurement: a runtime's
per-server MCP env block is **key-scoped**, proven on Claude Code 2.1.236 — a service reading a name
the block does not set inherits it from the environment instead. So a service declares exactly which
env names carry its endpoint rather than anyone guessing.

```json
{
  "version": 1,
  "services": {
    "aify-comms": {
      "endpoint": "http://localhost:8800",
      "endpointEnv": ["AIFY_SERVER_URL", "CLAUDE_MCP_SERVER_URL"],
      "mcp": [
        { "name": "aify-comms",         "command": "node", "args": ["<bridge>/server.js"] },
        { "name": "aify-comms-channel", "command": "node", "args": ["<bridge>/claude-channel.js"] }
      ]
    }
  }
}
```

- [ ] **Step 1: Write the failing tests.**

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRegistry, mcpEntriesFor, fingerprint } from "../lib/registry.mjs";

test("a service declaring endpointEnv gets those keys populated with its endpoint", () => {
  const { ok, registry } = parseRegistry(JSON.stringify({
    version: 1,
    services: { "aify-comms": {
      endpoint: "http://127.0.0.1:8800",
      endpointEnv: ["AIFY_SERVER_URL", "CLAUDE_MCP_SERVER_URL"],
      mcp: [{ name: "aify-comms", command: "node", args: ["/b/server.js"] }],
    } },
  }));
  assert.equal(ok, true);
  assert.deepEqual(mcpEntriesFor(registry)[0].env, {
    AIFY_SERVER_URL: "http://127.0.0.1:8800",
    CLAUDE_MCP_SERVER_URL: "http://127.0.0.1:8800",
  });
});

test("a service that declares no endpointEnv gets an EMPTY env, not a guessed one", () => {
  // Guessing would silently work for aify-comms and silently fail for anything else.
  const { registry } = parseRegistry(JSON.stringify({
    version: 1,
    services: { graph: { endpoint: "http://x", mcp: [{ name: "g", command: "node", args: [] }] } },
  }));
  assert.deepEqual(mcpEntriesFor(registry)[0].env, {});
});

test("malformed JSON reports an error and never throws", () => {
  const r = parseRegistry("{not json");
  assert.equal(r.ok, false);
  assert.ok(r.errors.length > 0);
});

test("an unknown top-level version is refused rather than best-guessed", () => {
  assert.equal(parseRegistry(JSON.stringify({ version: 99, services: {} })).ok, false);
});

test("fingerprint is stable across key order and changes when an endpoint changes", () => {
  const a = parseRegistry('{"version":1,"services":{"a":{"endpoint":"u","mcp":[]},"b":{"endpoint":"v","mcp":[]}}}').registry;
  const b = parseRegistry('{"version":1,"services":{"b":{"endpoint":"v","mcp":[]},"a":{"endpoint":"u","mcp":[]}}}').registry;
  const c = parseRegistry('{"version":1,"services":{"a":{"endpoint":"CHANGED","mcp":[]},"b":{"endpoint":"v","mcp":[]}}}').registry;
  assert.equal(fingerprint(a), fingerprint(b));
  assert.notEqual(fingerprint(a), fingerprint(c));
});

test("a MISSING registry is a valid empty registry, not an error", () => {
  // A host with no service installed is a legitimate state: the wrapper still installs.
  assert.equal(parseRegistry("").ok, true);
});
```

- [ ] **Step 2: Run them. Expected: FAIL, module not found.**
- [ ] **Step 3: Implement `lib/registry.mjs`.** Guards fail closed: an unparseable or unknown-version
      registry returns `ok:false`, never a partially-populated object.
- [ ] **Step 4: Run. Expected: PASS.**
- [ ] **Step 5:** Write `docs/REGISTRY.md` — the schema, who writes it, who reads it, and that it is
      read at install and not at launch.
- [ ] **Step 6: Commit.**

---

### Task 2: Harness detection

**Files:** Create `lib/detect-harnesses.mjs`, `tests/detect-harnesses.test.js`.

**Interfaces — Consumes:** nothing. **Produces:** `detectHarnesses(lookup) -> [{client, command, found}]`
where `lookup(cmd) -> string|null` is injected so the test never touches the real PATH.

The injected lookup is the point. A test that shells out to `command -v` measures the developer's
machine, and this project has a rule about tests that read live ambient state.

- [ ] **Step 1: Write the failing tests.**

```js
test("returns one row per known harness, found or not", () => {
  const rows = detectHarnesses((c) => (c === "claude" ? "/usr/bin/claude" : null));
  assert.deepEqual(rows.map((r) => r.client).sort(), ["claude", "codex", "hermes", "pi"]);
  assert.equal(rows.find((r) => r.client === "claude").found, true);
  assert.equal(rows.find((r) => r.client === "codex").found, false);
});

test("a lookup that throws is treated as NOT FOUND, never as found", () => {
  const rows = detectHarnesses(() => { throw new Error("PATH exploded"); });
  assert.equal(rows.every((r) => r.found === false), true);
});

test("the harness list is derived from the wrapper templates present, not hardcoded", () => {
  // A fifth wrapper template must not need this list edited to be installable.
  assert.equal(detectHarnesses(() => null).length, wrapperTemplateNames().length);
});
```

- [ ] **Step 2: Run. Expected: FAIL.**
- [ ] **Step 3: Implement.** Derive the client list from `wrappers/*.sh.in` rather than listing it.
- [ ] **Step 4: Run. Expected: PASS.**
- [ ] **Step 5: Commit.**

---

### Task 3: `install.sh --all`

**Files:** Modify `install.sh` (aify-wrapper). Modify `tests/render.test.js`.

`--client` stays and stays exact. `--all` installs a launcher for every detected harness and prints a
line per skipped one saying why. Silence about a skip reads as "installed everything" when it did not.

- [ ] **Step 1: Write the failing tests.** Assert that `--all` against a stub PATH containing two
      runtimes emits exactly two launchers, and that the two absent ones are NAMED in the output.
- [ ] **Step 2: Run. Expected: FAIL.**
- [ ] **Step 3: Implement**, reusing `--render-only` so the test never mutates the machine.
- [ ] **Step 4: Run. Expected: PASS.** Confirm `--render-only` still exits before any env mutation.
- [ ] **Step 5: Commit.**

---

### Task 4: Bake the registry fingerprint, and report staleness

**Files:** Modify `wrappers/*.sh.in` (both repos, byte-identical), `install.sh`, tests.

Add `HARNESS_REGISTRY_FINGERPRINT="@@REGISTRY_FINGERPRINT@@"` beside `HARNESS_WRAPPER_VERSION`.
`--check` prints it. A reader comparing it against the current registry can say **reinstall** —
by reading the file, never by running it.

- [ ] **Step 1: Write the failing tests.** `--check` exposes the fingerprint; a wrapper built from an
      empty registry and one built from a populated registry differ; `--check` still registers nothing
      and starts nothing.
- [ ] **Step 2: Run. Expected: FAIL.**
- [ ] **Step 3: Implement** in all four templates.
- [ ] **Step 4:** Prove each template change is otherwise byte-identical to the previous render before
      asserting new behaviour. This is how the extraction caught an escaped-backtick bug that had been
      silently blanking a comment in every installed hermes-aify.
- [ ] **Step 5:** Re-sync the four templates into aify-comms and update the hashes in
      `test_wrapper_templates_are_published_in_sync.py` **in the same commit**.
- [ ] **Step 6: Commit.**

---

### Task 5: Strict mode carries the services the operator opted in, not all of them

**Files:** Modify `wrappers/claude-aify.sh.in`, `lib/registry.mjs`, tests. Re-sync to aify-comms.

**PLAN CORRECTION, made while implementing Task 4.** This task originally read "strict mode emits one
entry per registered service". That is wrong, and the template says why in its own comment:

> Escape hatch: set `AIFY_CLAUDE_STRICT_MCP=1` ... Use this when the Claude Code MCP init race
> (upstream issues #38462, #21341) re-bites and channel notifications stop delivering because slower
> MCP servers leave `aify-comms-channel` stuck in "still connecting" state.

Strict mode exists **because** extra MCP servers cause the race. Emitting every registered service
would reintroduce exactly the failure the flag is the workaround for. The original task would have
shipped a plausible-looking regression, and the finding that strict mode "forbids multi-service" is
still true — it is a deliberate trade, not a defect.

**What to build instead.** A service opts in with `"strictMcp": true` in its registry entry. Default
absent means today's behaviour byte-for-byte: the two aify-comms entries and nothing else. An operator
who needs a second service inside strict mode says so, service by service, and owns the race risk for
the ones they named.

The primary two stay built at LAUNCH rather than baked, because `HARNESS_ENDPOINT` is a launch-time
input that must keep winning for them. Opted-in extras are baked at install as a JSON fragment.

- [ ] **Step 1: Write the failing tests.** A registry with an opted-in second service renders a strict
      config carrying both, each with its own endpoint in its own `endpointEnv` keys. A registry with
      no opt-in renders a strict config byte-identical to today's. `strictMcp: true` on a service with
      no `mcp` entries adds nothing rather than an empty object.
- [ ] **Step 2: Run. Expected: FAIL.**
- [ ] **Step 3:** Add `strictMcp` to the schema and a `strictMcpEntriesFor(registry)` selector.
- [ ] **Step 4: Implement the template change.** Prove the default-path render is byte-identical to
      the pre-change render before asserting the new behaviour.
- [ ] **Step 5: Run. Expected: PASS.** Default (non-strict) mode must still write no MCP config at all.
- [ ] **Step 6:** Re-sync to aify-comms and update the hashes in the same commit.
- [ ] **Step 7: Commit.**

### Task 6a: aify-comms registers itself — DONE

**Files:** `mcp/stdio/service-registry.mjs` (pure), `mcp/stdio/register-service-cli.mjs` (wiring),
tests for both, `install.sh`, `mcp/stdio/aify-service-endpoint.mjs`.

Installing aify-comms writes its own entry into `~/.aify/services.json`, which is the half the
operator asked for by name: installing a SERVICE registers it; installing the wrapper package is never
the goal.

- [x] The endpoint env names are exported from the bridge's own resolver as `ENDPOINT_ENV_NAMES` and
      imported by the writer, rather than typed twice. A name the bridge reads but the registry does
      not declare is INHERITED from whatever launched the runtime, because a per-server MCP env block
      is key-scoped — a failure that looks like everything working until two services disagree.
- [x] aify-comms owns its key and nothing else. An unreadable or wrong-version registry is REFUSED,
      not rewritten: overwriting would uninstall another service at the moment somebody reinstalls
      something unrelated. Mutation-checked.
- [x] Registration failure is non-fatal and says so. Launchers not learning about this service is not
      the same as this service being broken.
- [x] Two runs leave the file byte-identical, so a reinstall does not change the fingerprint baked
      into every launcher.
- [x] **Cross-repo contract verified by running it:** the file aify-comms writes parses in
      aify-wrapper's `lib/registry.mjs`, yielding both MCP entries with the endpoint under both
      declared keys.

### Task 6b: aify-comms consuming the package — NEEDS AN OPERATOR DECISION

**Not done, and not something to decide unilaterally.** The plan said "point aify-comms' install at
the aify-wrapper package, then delete `wrappers/` and the drift gate". Implementing it means choosing
how aify-comms LOCATES the package, and every option changes how a user installs this product:

| option | cost |
|---|---|
| **git submodule** | every clone needs `--recurse-submodules`; a fresh clone without it fails at install |
| **npm dependency on the GitHub URL** | install now needs network, and a pinned sha to avoid drift |
| **prefer an installed `aify-wrapper`, fall back to the bundled copy** | no new dependency, but an OLD installed package would silently produce OLD launchers — the exact version-skew class this repo builds gates against |
| **keep the copy + the drift gate** (today) | two sources of truth, one test making a change hurt in both places |

The third looks attractive and is the most dangerous: it reintroduces skew invisibly, and the drift
gate exists precisely because a launcher shipping behaviour that was already corrected elsewhere is
this project's signature failure.

Until the operator picks one, the templates stay duplicated and
`test_wrapper_templates_are_published_in_sync.py` stays. Its own docstring already says to delete it
the day the duplication ends — that day is a decision, not a task.

## Phase gate

Done when, by measurement:

1. `install.sh --all` on a host with N harnesses produces N launchers and names every skip.
2. A launcher built against a stale registry is REPORTED stale by `aify-wrapper-check`, which reads
   the launchers and never runs one. (`--check` prints the fingerprint; printing is not comparing,
   and this clause stood marked met for a while with no consumer anywhere.)
3. Strict mode carries every registered service with its own endpoint.
4. `test_wrapper_templates_are_published_in_sync.py` is deleted, because the duplication is gone.
5. All four suites green, counts recorded from the run.

## Deliberately not in this phase

- **aify-env.** Phase 7.
- **Removing the `aify-comms` command.** Phase 8 — it is the environment bridge and nothing replaces
  it until aify-env exists.
- **Reading the registry at launch.** Rejected above on latency, and reinstall-to-refresh matches how
  every other install-time value in this project already behaves.
