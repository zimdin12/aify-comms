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
- **codex, hermes:** role only, with the same precedence, and a notice when the definition carries a
  model or effort. Neither launcher handles model or effort today. codex runs its TUI against an
  app-server, and hermes reaches its gateway-host path only with no extra arguments, so an added `-m`
  would move every defined hermes agent off that path. How each runtime takes them is known (codex
  `-m` and `-c model_reasoning_effort=...`; hermes `-m`/`HERMES_INFERENCE_MODEL` and `--reasoning`,
  from its own `--help`), but where they take effect on those two paths was not verified, so they
  are not applied. This is a decision for the operator.

Rules from C9 as built: missing is today's behaviour; an invalid file, an unreadable one, a non-regular
one, or one for another harness refuses with 78 unless `--aify-ignore-definition`; a managed launch
(`AIFY_MANAGED_VIA_WRAPPER=1`) reads no file; the file is never written. Two choices of mine:

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
