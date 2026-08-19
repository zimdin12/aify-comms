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
| Client | 4 wrapper generators in `install.sh`, 3,943 lines | Not yet — 108 `aify-comms` references, 99 `AIFY_AGENT_ID` |
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

| Generator in `install.sh` | Lines | of which comment |
|---|---|---|
| `install_claude_wrapper` | 128 | 30 |
| `install_codex_wrapper` | 10 | 3 |
| `install_pi_wrapper` | 222 | 76 |
| `install_hermes_wrapper` | 251 | 86 |
| **total** | **611** | — |

**611 lines, ~14% of install.sh's 4,371.** The wrapper generators are small and roughly comparable;
hermes is barely larger than pi.

**A RETRACTION, kept here because the wrong number was already committed.** An earlier version of this
spec said claude 358 / codex 321 / pi 417 / hermes **2,847**, totalling 3,943, and concluded "hermes is
65% of the installer and must be sequenced LAST". That was a measurement bug of mine, not a fact about
hermes: my script terminated each generator's span at the NEXT WRAPPER INSTALLER, and hermes is the last
one — so its span ran to end-of-file and swallowed every other function in the script. Terminating at the
next function of ANY name gives the table above. `docs/V0.6_PLAN.md`'s original "~100 lines of shell" was
far closer to right than my correction, and the sequencing argument built on 2,847 has no basis.

**Where hermes IS heavy, and it is not the wrapper.** Its total footprint across `install.sh` is ~1,145
lines, the largest of any runtime, but it lands in shims rather than in the launcher:

| Lines | Function | What it is |
|---|---|---|
| 503 | `install_hermes_windows_tui_shim` | the `.ps1` wrapper — 187 of them comments. Handles PowerShell 5.1 decoding a BOM-less `.ps1` as the system codepage, points `HERMES_TUI_DIR` at a prebuilt bundle so a managed `hermes --tui` skips a per-launch `npm run build`, and hides the loop process window |
| 277 | `aify_hermes_kill_prior` | process-tree cleanup via `Win32_Process` |
| 251 | `install_hermes_wrapper` | the launcher itself |
| 114 | `_patch_hermes_config_at` | config patching |

That is the cost of the visible-TUI hard requirement on Windows plus hermes having no clean process
lifecycle of its own — hermes is the only runtime needing a `.ps1` at all. Whether 503 lines is the
right price is a fair Phase 2 question; it is not dead code.

**What this means for sequencing:** nothing forces hermes last on size grounds. If it is sequenced last
it should be for the TUI shim and the process-management coupling, which are real, and not for a line
count that was never true.

---|---|
| `install_claude_wrapper` | 358 |
| `install_codex_wrapper` | 321 |
| `install_pi_wrapper` | 417 |
| `install_hermes_wrapper` | **2,847** |
| **total** | **3,943 of install.sh's 4,370** |

**Hermes is 65% of the installer on its own.** Any plan that treats "the wrappers" as one small uniform
thing is planning for a file that does not exist. Whatever Phase 2 does, hermes is the bulk of it and
should be sequenced last, after the shape is proven on the two small ones.

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
- **Session-handle recovery.** The claude wrapper's `--resume` lookup calls the aify service directly.
  Under the contract that becomes a host-supplied hook (`HARNESS_IDENTITY_RESOLVER`, a command the
  wrapper may invoke), or it stays behind in aify-comms' own generator. **Open question — needs a
  decision before implementation**, and it is the single largest service assumption in the wrappers.
- **Anything hermes-specific about gateways.** The 2,847-line hermes generator carries gateway plumbing
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

---

## Open questions, to answer before implementation

1. **Session-handle recovery** (above): host hook, or left behind in aify-comms?
2. **Does the wrapper package own MCP-config writing?** Every client's config format differs
   (`.mcp.json`, `claude mcp add`, hermes' own). That is per-client knowledge, and it may belong to the
   host rather than the wrapper.
3. **Distribution.** npm package, or a copied dotfolder as now? A dotfolder keeps the load-time fix and
   the same-build guarantee; a package gets versioning and reuse. They pull in opposite directions.
4. **Does hermes come along at all in v0.6?** At 2,847 lines with gateway plumbing inside, it may
   deserve its own release rather than riding this one.

---

## Why this spec exists before any file moves

The failure it prevents is the one the plan names: a wrapper extracted first and contracted afterwards
is a wrapper still shaped like aify-comms — installable elsewhere in form, useless there in fact. Every
row in "what the host provides" is a line item that has to stop being hardcoded, and the count of
service-specific tokens above is the size of that job.
