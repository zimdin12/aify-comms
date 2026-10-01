# P6 evidence: the launcher reads its agent's definition as defaults (C9)

P6 is aify-wrapper's, built on `~/projects/aify-wrapper-next` (branch next/env-owned-agents). Its
proof lives here with the rest of the tag.

## What it adds

| file | what |
|---|---|
| `lib/agent-definition-schema.mjs` | aify-env's validator, a COPY held to aify-env's bytes (not a port) |
| `lib/agent-definition-defaults.mjs` | `readDefinition`: found, missing, or refused (not admitted, not a regular file, unreadable, invalid, another harness); `shellAssignments`; `definitionsDir` |
| `bin/aify-definition.mjs` | the command a launcher runs: assignments and 0, or the problems and 78 |
| `wrappers/{claude,codex,hermes}-aify.sh.in` | `aify_read_definition`, called by `--check` and after the argument loop; `--aify-ignore-definition` |

What each launcher applies:

- **claude:** role, model and effort. `--aify-role` > `HARNESS_ROLE` > `AIFY_AGENT_ROLE` > the
  definition > `coder`; `--model` > `AIFY_MANAGED_MODEL` > the definition; the same for effort.
  `--check` prints the definition, model and effort it resolved.
- **codex, hermes** (as first built; REVISED below, R1): role only, with the same precedence, and a notice when the definition carries a
  model or effort. Neither launcher handles model or effort today. codex runs its TUI against an
  app-server, and hermes reaches its gateway-host path only with no extra arguments, so an added `-m`
  would move every defined hermes agent off that path. How each runtime takes them is known (codex
  `-m` and `-c model_reasoning_effort=...`; hermes `-m`/`HERMES_INFERENCE_MODEL` and `--reasoning`,
  from its own `--help`), but where they take effect on those two paths was not verified, so they
  are not applied. This is a decision for the operator.

Rules from C9 as built: missing is today's behaviour; an invalid file, an unreadable one, a non-regular
one, or one for another harness refuses with 78 unless `--aify-ignore-definition`; a managed launch
(`AIFY_MANAGED_VIA_WRAPPER=1`) reads no file; the file is never written. Two choices of mine, BOTH
REVERSED by the review (see the revision below):

- **An install without the reader** (a bridge directory whose aify-wrapper predates P6) launches as
  before and says on stderr that the definition is not applied. Refusing there refused every agent-id
  launch in ten existing tests, which render against such a directory; a launcher must not be refused
  for a file it cannot even look for.
- **An id recovered later** from a resume handle gets no defaults: the definition is read for the id
  known from a flag or the environment.

## Witnesses (aify-wrapper `tests/`)

- `agent-definition-defaults.test.js`: every export, every outcome, and bash reading an assignment
  back exactly (a quote and `$(x)` in a value).
- `a-launcher-takes-defaults-from-its-definition.test.js`: rendered launchers, a sealed PATH, a home
  and a definitions directory of their own, run with `--check`. claude: the definition's three values;
  each level of precedence; missing; a managed launch reading no file even when it is invalid; an
  install without the reader. All three: invalid and another harness refuse with 78, and the ignore
  flag starts without the file. codex and hermes: `--check` takes the role from the definition, and
  the launch path's read, apply and export are pinned in order (structurally, since that path starts
  an app-server or a gateway host). The loop consuming the ignore flag is pinned for all three.
- `the-definition-schema-is-aify-envs.test.js`: the copy is aify-env's bytes; aify-env's shared
  fixture passes through it; and in a child whose home is a temporary directory, what aify-env's real
  store writes, the reader finds, by default and with the override. It SKIPS BY NAME with no aify-env
  checkout carrying the schema (the suites here set `AIFY_ENV_REPO` to aify-env-next).

## Mutation battery

`mutations-p6.json`, run with the P2 driver against aify-wrapper-next. Before it ran, two mutants were
predicted to survive and got witnesses: swapping the managed and definition model (the managed test also
stopped the read), and the loop no longer consuming the ignore flag. Result in `mutations-p6-result.txt`.

## Also seen

The `codex` on this shell's PATH (`C:/ProgramData/nvm/v22.20.0/node_modules/@openai/codex`) does not
start: "Missing optional dependency @openai/codex-win32-x64". The operator's agents may start codex from
another install; this was not checked further.

## Revision after review (REVISE of 57400c7 / bf75574f): R1-R4

The review's four findings, and the owner's ruling that settled R1 (P0 C9, "Owner ruling, 2026-10-01").

**R2, a launcher that cannot run the reader started anyway.** Now it refuses with 78, as an invalid file
does, unless `--aify-ignore-definition` or a managed launch: not being able to look is not finding
nothing. Sixteen older tests failed on that: six resume tests whose temporary bridge had no reader, and
ten (render, registry-baking, strict-mcp, shared) that rendered against install.sh's default bridge,
`~/.aify-comms/mcp/stdio`, and passed only because the installed bridge had no reader yet; after the next
install those ten would have read the operator's real definitions. All now render against a bridge built by
`tests/definition-reader-bridge.mjs`, with a definitions directory of their own. The reader-less
witness that pinned the old behaviour is replaced by one that pins the refusal, with the ignore,
managed and no-id controls, for all three launchers.

**R3, an id recovered from a resume handle got no definition.** codex and hermes read the definition
after recovery, before anything is exported or started; claude reads again after recovery when the id
changed, recomputing from the values the flags and environment gave, so the first read's defaulted role
cannot pass for a given one. Witnessed through the whole launch to a stub runtime
(`a-resumed-session-finds-its-agent-through-the-bridge.test.js`, on the shared
`tests/launch-to-a-stub-runtime.mjs`): a recovered agent's definition applied, a role flag still beating
it, an invalid file refused with nothing started, and a missing reader refused.

**R4, codex and hermes `--check` reported the wrong role.** Their `--check` was a pre-scan that read only
the identity flags. It now runs after the real argument loop through the same function as the launch,
so it reports what the launch would use. Both `--aify-role` spellings against the definition and against
`HARNESS_ROLE`, for all three launchers. codex's app-server port and log directory moved below the check,
which therefore still writes nothing.

**R1, model and effort on codex and hermes.** Each mechanism observed on the runtime:

- codex 0.159.3 (`codex-model-probe.mjs`, `codex-model-probe-result.json`): a sealed app-server and the
  real TUI in a PTY behind a logging proxy. With `-c model="srv-model"` and
  `-c model_reasoning_effort="low"` on the app-server and config.toml saying cfg-model/medium, the TUI
  read the app-server's config and started its thread on srv-model/low. `-c` on the TUI alone gave
  tui-model/high; `-m flag-model` on the TUI beat the app-server's model. The proxy answered the TUI's
  `account/read` as an API-key account, the only rewrite, because the sealed home has no credentials and
  the TUI otherwise stops at sign-in; no turn was sent. So codex-aify puts the pairs in
  `CODEX_APP_SERVER_CONFIG` on the app-server lines (agreed with dashboard-manager, whose MCP pairs go
  below), and none when the operator's own `-m`/`--model`/`-c` gives one.
- hermes 0.21.5 (`hermes-model-probe.mjs`, `hermes-model-probe-result.json`): a sealed `hermes dashboard`
  (the gateway host aify-comms runs) with its own HERMES_HOME. Started with
  `HERMES_INFERENCE_MODEL=probe/env-model`, `session.create {}` answered model probe/env-model.
  `session.create {model, reasoning_effort: low}` gave that session low. `config.set reasoning xhigh` on
  a session moved `config.get reasoning` for it from medium to xhigh. The same call for an unknown
  session answered `4001 session not found` and the host-wide effort stayed medium: a miss is never a
  global write. hermes has no launch-time effort lever on the gateway path (`--reasoning` reaches only a
  one-shot or the classic CLI; the TUI has no variable for it). So hermes-aify exports the model as
  HERMES_INFERENCE_MODEL and the effort as AIFY_HERMES_SESSION_EFFORT (always written, so an inherited
  value never applies), and aify-comms' delivery loop sets it once on each live session
  (`mcp/stdio/hermes-session-effort.mjs`).
- NOT PROVEN, and said in C9: a RESUMED hermes session keeps the model it was stored with (hermes' rule,
  read in its source, not exercised), and whether a session-scoped effort survives the lazy build of a
  resumed session with stored runtime overrides was not exercised: that needs a real resumed session,
  which is the operator's relaunch of one hermes agent after install.

Witnesses: `codex-takes-model-and-effort-on-its-app-server.test.js` and
`hermes-takes-model-and-effort.test.js` (executed to stub runtimes; hermes on its plain path, since the
gateway path starts a gateway host, and the two variables are exported before the path is chosen);
`--check` lines in `a-launcher-takes-defaults-from-its-definition.test.js`; aify-comms
`tests/hermes-session-effort.test.js` and the loop's call site in `tests/hermes-managed-host.test.js`.

Also pinned, from dashboard-manager's measurement on Claude Code 2.1.286 that
`--dangerously-load-development-channels server:x PROMPT` swallows the prompt: the word after the
channel's value is always `--settings`, a one-value flag
(`claude-user-arguments-are-never-a-channel-name.test.js`). It does not happen today; nothing held it.

### Mutation batteries

`mutations-p6r.json` (aify-wrapper) and `mutations-p6r-bridge.json` (aify-comms), with the P2 driver.
The first run of the wrapper battery filtered the resume tests with `--test-name-pattern="ecovered"`,
which is case-sensitive and missed every "RECOVERED" test: two claude R3 mutants survived the FILTER, not
the suite, and one hermes mutant hung while the hermes probe was installing its runtime on the same host.
The filter is now `/recovered/i` and the battery was run again in full. codex's `setsid` app-server line
is not mutated: this host has no `setsid`, so the line it runs is the other one, which is.

Results, each in its `-result.txt` beside its catalog:

- `mutations-p6r.json`, aify-wrapper: 36/36 killed, every one exit 1, including the two claude R3 mutants
  that survived the case-sensitive filter (now killed by "applies the RECOVERED agent's definition").
- `mutations-p6r-bridge.json`, aify-comms bridge: 6/6 killed.
- `mutations-c12.json`, aify-comms service (C12: the defaults resolver, `runsWith`, the effort route):
  11/11 killed, each by the test its name points at. The test command passes unmutated (16 tests, 4
  subtests) as its positive control.
