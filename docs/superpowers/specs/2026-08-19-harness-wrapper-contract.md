# The harness wrapper contract — what a wrapper is, and what a host must give it

**Status: SPEC, not implementation.** Phase 2 of the v0.6 plan requires this written and agreed before
any file moves. The plan's own words: "The refactor is easy once the contract is named; doing it in the
other order produces a wrapper still shaped like aify-comms."

**Scope decision, made by the operator 2026-08-19: the WRAPPERS become the published, independently
installable thing.** The bridge (`mcp/stdio`) stays in this repo. I recommended the opposite — the
bridge is already an npm package and carries all 36 MCP tools — and recorded the risk that a host
installing a launcher alone still cannot send data or read status. The operator chose wrappers-only
with that risk stated. This spec therefore designs the version of wrappers-only that is **not**
cosmetic.

---

## The target architecture this is the first step toward (operator, 2026-08-19)

Three separable tiers, with the server able to contain the other two so a single-box install stays one
install:

```text
   CLIENT                    ENVIRONMENT                    SERVER
   what an agent runs        manager / bridge               the control plane
   ─────────────────         ────────────────────           ──────────────────
   claude-aify               environment bridge             FastAPI + SQLite
   codex-aify                managed workers                dispatch + contracts
   hermes-aify               terminals / PTYs               dashboard
   pi-aify                   spawn + control loops          status engine
        │                          │                              │
        └──────────────────────────┴──────────────────────────────┘
                     any tier may be LOCAL or REMOTE

   The SERVER ships with an environment and a client inside it, so one box works
   out of the box — while EXTERNAL environments and clients connect to the same
   surfaces.
```

**Why this makes wrappers-first the right first slice, and why my "cosmetic" objection was wrong.** I
argued against wrappers-only because a host installing a launcher alone cannot send data or read
status. That is true of wrappers-only as a FINAL state. As the first slice of a three-tier split it is
the correct starting point: the client tier is the smallest, the least coupled, and the only one whose
extraction cannot destabilise a live fleet. The bridge carries registration, dispatch delivery and PTY
ownership for 21 live agents; going there first would put the riskiest tier first.

**What each tier maps to today, measured:**

| Tier | Today | Separable? |
|---|---|---|
| Client | 4 wrapper generators, **1,554 lines** (claude's body now in `wrappers/`) | Started — claude is on the contract; 108 `aify-comms` references remain |
| Environment | `mcp/stdio` in its environment-bridge role: spawn loop, terminal controls, managed workers | Partly — already one npm package, but the role is a flag on the same binary |
| Server | `service/` — FastAPI, SQLite, dispatch, dashboard | Yes — already a container with an HTTP surface |

**The consequence for THIS spec:** every `HARNESS_*` input below is really a client↔environment
boundary being named for the first time. `HARNESS_MCP_COMMAND` is the client saying "point me at an
environment" without knowing which one — which is exactly what "external environments can connect
also" requires. So the contract is not wrapper bookkeeping; it is the first written edge of the
three-tier shape.

**What this spec deliberately does NOT decide:** how a REMOTE environment registers with the server, or
how the server's built-in environment is packaged versus an external one. Those are the
environment↔server edge, and naming them here would be inventing a contract for a tier nobody has
started. They are v0.7 subjects that this edge should not prejudge.

---

## What a wrapper actually is — measured, then RE-measured 2026-08-19

| Generator in `install.sh` | Lines |
|---|---|
| `install_claude_wrapper` | 349 (now `wrappers/claude-aify.sh.in` + 14 lines of renderer call) |
| `install_codex_wrapper` | 320 |
| `install_pi_wrapper` | 199 |
| `install_hermes_wrapper` | 686 |
| **total** | **1,554** |

**~1,554 lines, about a third of install.sh.** Hermes' launcher is the largest by a wide margin —
more than double pi's — and that is before its shims.

**MEASURED THREE TIMES, WRONG TWICE, so the method is recorded with the answer.** Both wrong answers
came from terminating the span on something that also occurs INSIDE the generated wrapper:

1. *claude 358 / codex 321 / pi 417 / hermes **2,847**.* The span ended at the next WRAPPER installer,
   and hermes is the last one, so its span ran to end-of-file and swallowed every other function in
   the script. A "hermes is 65% of the installer" sequencing argument was built on it.
2. *claude 128 / codex **10** / pi 222 / hermes 251, total 611.* The correction of (1). The span ended
   at the next `name() {` of ANY name — but a wrapper DEFINES ITS OWN FUNCTIONS, so codex's span ended
   at `pick_port()`, which is part of the wrapper body. Ten lines for a 320-line generator.

The figures above track heredoc state — `<<TOKEN`, `<<'TOKEN'` and `<<-TOKEN` open, a matching line
closes — so neither a `}` nor a `name() {` inside a wrapper body can end a generator early. Each
boundary was then read by eye: all four end with an `install_windows_cmd_shim` call followed by `}`.

**What changes because of this.** The previous version of this section concluded "the wrapper
generators are small and roughly comparable; hermes is barely larger than pi" and that "nothing forces
hermes last on size grounds". Both are withdrawn. Hermes' launcher is 686 lines against pi's 199, and
sequencing it last is supported by size as well as by shim coupling. The ordering decision does not
change — it was made on coupling — but the reasoning offered for it was wrong.

**Hermes is heavy in the launcher AND beside it.** Total footprint ~1,301 lines, the largest of any
runtime by far:

| Lines | Function | What it is |
|---|---|---|
| 686 | `install_hermes_wrapper` | the launcher itself — the largest of the four, and it CONTAINS `aify_hermes_kill_prior` (67 lines of process-tree cleanup, defined inside the generated wrapper) |
| 502 | `install_hermes_windows_tui_shim` | the `.ps1` wrapper. Handles PowerShell 5.1 decoding a BOM-less `.ps1` as the system codepage, points `HERMES_TUI_DIR` at a prebuilt bundle so a managed `hermes --tui` skips a per-launch `npm run build`, and hides the loop process window |
| 113 | `_patch_hermes_config_at` | config patching |

An earlier version of this table listed `aify_hermes_kill_prior` at 277 lines as a SEPARATE item beside
a 251-line launcher. It is 67 lines and it is *inside* the launcher — the row was both wrong and double
counted, which is why the totals never added up against the generator table above.

That size is the cost of the visible-TUI hard requirement on Windows plus hermes having no clean
process lifecycle of its own — it is the only runtime needing a `.ps1` at all. Whether 502 lines is the
right price is a fair Phase 2 question; it is not dead code.

**What this means for sequencing:** hermes goes last for two reasons that agree. Its launcher is the
largest of the four at 686 lines, and it is the one coupled to shims — the 503-line Windows TUI shim
and the 277-line process-tree cleanup — that no other runtime needs. The coupling is the deciding
reason; the size merely stopped contradicting it once measured correctly.

**They are saturated with this service's concepts**, which is the real work of making them
independently installable — not moving files:

| Token | Occurrences in `install.sh` |
|---|---|
| `aify-comms` | 108 |
| `AIFY_AGENT_ID` | 99 |
| `AIFY_COMMS_URL` | 30 |
| `/api/v1` | 13 |
| `comms_register` | 6 |

---

## What a wrapper DOES, in four jobs

Derived by reading the generators, not from memory. Only the first is service-agnostic today.

1. **Resolve the runtime CLI.** Find `claude` / `codex` / `hermes` / `pi` on PATH, handle the Windows
   `.cmd` shim, fail with a message naming what is missing.
2. **Export the identity environment.** 17 `AIFY_*` variables: agent id, role, runtime, session mode,
   cwd, channels-enabled, service URL, and per-runtime extras.
3. **Point the runtime's MCP config at a bridge.** Write/patch the client's MCP config so the runtime
   loads the bridge as an MCP server.
4. **Launch the runtime**, forwarding argv, and on some paths recover an agent id from a resumed
   session (the claude wrapper does a service lookup and a temp-store scan for `--resume`).

Job 4's recovery paths are where the service assumptions bite hardest: the claude wrapper calls the
aify service to resolve an agent id from a session handle.

---

## The contract

### What the HOST provides

A host is whatever installs the wrapper — aify-comms today, another service tomorrow.

| Input | Meaning | Required |
|---|---|---|
| `HARNESS_ENDPOINT` | Base URL of the coordinating service | yes |
| `HARNESS_MCP_COMMAND` | The command that starts the MCP bridge the runtime should load | yes |
| `HARNESS_IDENTITY` | Opaque id for this agent/session, exported to the runtime | yes |
| `HARNESS_ROLE` | Opaque role string | no |
| `HARNESS_CWD` | Working directory the runtime starts in | no |
| `HARNESS_EXTRA_ENV` | `KEY=VALUE` pairs exported verbatim before launch | no |

**`HARNESS_MCP_COMMAND` is the pivot.** It is what makes the wrapper reusable: today the wrapper knows
it must point at `~/.aify-comms/mcp/stdio/server.js`. Under the contract it points at whatever the host
names. A host with a different bridge gets working launchers; aify-comms passes its own bridge and
nothing changes.

### What the host GETS

- A launcher on PATH per runtime that resolves the CLI, exports the identity env, wires the MCP config,
  and execs the runtime with argv forwarded.
- A documented exit-code contract: `0` runtime exited normally; `127` runtime CLI not found; `78`
  configuration invalid (missing required input). Anything else is the runtime's own code, passed
  through unchanged.
- A `--check` mode that validates resolution and configuration **and starts nothing**. This is not
  optional politeness: this repo's standing rule is "never run a bare `aify-comms`" precisely because
  running the launcher to see if it works registered a bridge and reaped a live fleet.

### What is explicitly NOT in the contract

- **Registration, heartbeat, status and dispatch.** Those are the BRIDGE's surface, and the bridge is
  staying here. A wrapper never speaks to the service about them.
- **Session-handle recovery. DECIDED 2026-08-19: it stays behind in aify-comms' own generator.** The
  claude wrapper's `--resume` lookup calls the aify service directly, and it remains an aify-comms
  concern; the generic wrapper ships no resume recovery and the contract defines no
  `HARNESS_IDENTITY_RESOLVER`. The consequence, stated so nobody rediscovers it: a different host
  gets working launchers but no `--resume`, and adding one later is a contract addition rather than a
  change. That is the right trade while the resolver has exactly one implementation — designing a
  hook against a single caller invents an interface instead of extracting one.
- **Anything hermes-specific about gateways.** Hermes' shims — the 503-line Windows TUI shim and the
  277-line process-tree cleanup — carry plumbing
  that is not a launcher concern. Sequencing it last is a consequence of this line.

---

## Version skew — the thing this cannot break

`install.sh` currently guarantees wrapper and bridge are the same build, by generating one and copying
the other in a single step. A standalone wrapper install breaks that guarantee, and
`aify-comms doctor`'s `bridge-installed` and `bridge-current` checks exist **because that guarantee kept
being violated silently** — a wrapper executing code it loaded at boot while newer code sat on disk.

The contract therefore requires:

- the wrapper reports its own version on `--check`;
- the host can compare it against what the bridge expects;
- `doctor` gains a wrapper-version check, or `bridge-installed` is extended to cover a wrapper that no
  longer ships with the bridge.

**Deploy coupling is a release step, not a doctor line.** comms-senior-dev, 2026-08-18: "doctor red +
relaunch instruction is not itself a deployment gate."

---

## The load-time constraint that is easy to lose

`install.sh` copies `mcp/stdio` + `node_modules` into `~/.aify-comms` and points every wrapper at that
native copy, because the repo often sits on a 9p/WSL2 mount where the bridge takes ~5s to load — and
hermes' MCP discovery window is a hardcoded 0.75s. A wrapper that points at a slow path produces a
hermes that silently has no tools.

Any wrapper package must keep pointing at a fast local copy, or the contract must say the host is
responsible for providing one. **This is not a performance nicety; it is the difference between a
working hermes and a mute one.**

**Decision 2 below chose an npm package, which is exactly what this constraint pushes against.** The
reconciliation — publish to npm, install into `~/.aify-comms`, and make the host responsible for a
fast local path if it installs elsewhere — is recorded there rather than here, because it is a
consequence of the distribution choice and must not read as an independent rule someone can drop.

---

## Decisions — operator, 2026-08-19

All four open questions are answered. They are recorded with their consequences, including the one
that pulls against a constraint elsewhere in this spec.

### 1. Session-handle recovery stays in aify-comms

See "What is explicitly NOT in the contract" above.

### 2. Distribution: npm package, installed into the native dotfolder

**This is the decision that pulls against the load-time constraint below, and the reconciliation is
part of the decision, not a workaround.** An npm package that a host `npm install`s onto a 9p/WSL2
path reintroduces the ~5s bridge load that hermes' hardcoded 0.75s MCP-discovery window cannot
survive — the failure mode being a hermes that starts fine and silently has no tools.

So: **published to npm, and `install.sh` installs it INTO `~/.aify-comms` (or `$AIFY_HOME`)** rather
than pointing any wrapper at a repo checkout. The package is the unit of versioning and reuse; the
dotfolder remains the unit of execution. A host that installs it elsewhere is responsible for putting
it on a fast local filesystem, and the contract says so.

**What this costs, stated plainly:** `install.sh`'s same-build guarantee — one step generating the
wrapper and copying the bridge — is broken by construction, because the two now version separately.
That guarantee is not decoration; `bridge-installed` and `bridge-current` exist because it kept being
violated silently. It is replaced by two things, both required before the package ships:

- the wrapper reports its own version on `--check`;
- `doctor` gains a wrapper-version check comparing it against the bridge's expectation.

Without those, this decision trades a guarantee for a package and gets a silent skew class back.

### 3. MCP-config writing: capability in the package, decision with the host

The operator's question was "why is it needed — only for install?", and the answer is **yes, install
only.** Measured: the wrapper GENERATORS never touch MCP config. It is written by separate
install-time functions (`install_claude_config` merging `.claude.json`'s `mcpServers`, and
`codex mcp add` / `claude mcp add --scope user`). No wrapper writes config at launch; a wrapper execs
the runtime and nothing else.

So the separation already exists in the code, and the operator's framing settles which side owns it:
*the wrapper is the thing a harness attaches to; aify-comms is the comms service that uses that
wrapper.* Pointing a client at one particular bridge is the HOST's act of attaching its own service —
not something a launcher should decide.

**Therefore:** the wrapper package SHIPS the per-client config knowledge as an explicit, separately
invocable `--configure` step, so no host has to reimplement four config formats. The host decides
whether and when to call it. The wrapper never calls it implicitly, and never at launch.

### 4. Runtimes in v0.6: claude, codex and pi. Hermes last.

Pi's launcher is 222 lines with no shims, which puts it in the same class as the other two. Hermes is
deferred **on shim coupling, not on size** — its 503-line Windows TUI shim and 277-line process-tree
cleanup serve the visible-TUI hard requirement and have no clean process lifecycle to lean on. That
is its own release. The retracted 2,847-line figure plays no part in this ordering.

---

## Why this spec exists before any file moves

The failure it prevents is the one the plan names: a wrapper extracted first and contracted afterwards
is a wrapper still shaped like aify-comms — installable elsewhere in form, useless there in fact. Every
row in "what the host provides" is a line item that has to stop being hardcoded, and the count of
service-specific tokens above is the size of that job.
