# aify-comms — Claude Code project notes

Inter-agent communication hub: messaging, channels, file sharing, active dispatch, and a dashboard for Claude Code, Codex, OpenCode, and other MCP-connected coding agents. This file is loaded by Claude Code when working **on this repo itself**; usage docs for someone installing aify-comms live in [README.md](README.md).

## Primary entry points

- [README.md](README.md) — what the service is, setup, day-to-day usage.
- [install.claude.md](install.claude.md) / [install.codex.md](install.codex.md) / [install.hermes.md](install.hermes.md) / [install.opencode.md](install.opencode.md) / [install.pi.md](install.pi.md) — per-runtime install guides (wrappers, hooks, verification).
- [DECISIONS.md](DECISIONS.md) — rationale for non-obvious design choices and current runtime limits.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md) — known limitations, deferred work, watch-items, and pre-existing backlog.
- `.claude/skills/aify-comms/SKILL.md` — agent-facing usage guide (tool reference, multi-instance matrix, status table).
- `.claude/skills/aify-comms-debug/SKILL.md` — known issues and fixes (AbsolutePathBuf, hard-reset sequence, buffer_full, orphaned runs, stale bridges).

## Developing on this repo

```bash
git pull
docker compose up -d --build            # rebuilds the Python service container
curl http://localhost:8800/health        # should return {"status":"healthy"}
```

Changes under `service/`, `mcp/sse_server.py`, and `config/` are COPY'd into the container image — rebuild after editing any of them. Changes under `mcp/stdio/` affect host-side bridges and MCP client sessions, so reinstall/restart `aify-comms`, `codex-aify`, or `claude-aify` after editing them. Changes to docs, skills, `install.sh`, and `.claude/` do not need a container rebuild, but installer changes require rerunning `install.sh`.

The MCP stdio bridges under `mcp/stdio/` run on the **host**, not in the container. They are loaded by Claude Code / Codex at startup, so changes there require restarting the client wrapper (`claude-aify` / `codex-aify`) — not a container rebuild.

## Repo layout (what matters)

| Path | What |
|------|------|
| `service/` | FastAPI backend, SQLite persistence, dashboard HTML, dispatch logic. Rebuild container after changes. |
| `mcp/stdio/` | Host-side MCP bridges (`server.js`, `claude-channel.js`, `runtimes.js`, `runtime-markers.js`, `notify-check.js`). Restart client wrapper after changes. |
| `mcp/sse_server.py` | SSE MCP transport (runs inside the container). Rebuild container after changes. |
| `.claude/skills/aify-comms/` | Usage skill — tool reference, workflow, status table, multi-instance matrix. |
| `.claude/skills/aify-comms-debug/` | Troubleshooting skill — known issues and fixes. |
| `.agents/skills/aify-comms*/` | Mirrors of the two skills for Codex agents. Keep in sync. |
| `install.sh` | Client installer. Targets Claude, Codex, or Hermes via `--client` (OpenCode/Pi installs are intentionally disabled). |
| `examples/team-setup/` | Example team definition (manager, coder, tester, etc.) showing how to register a multi-role team. |

## Development notes

- **`install.sh` copies the bridge runtime into a native dotfolder.** The installer copies `mcp/stdio` + its `node_modules` into `~/.aify-comms/` (override with `AIFY_HOME`) and points every wrapper + MCP config at that native copy, not at the repo checkout. Reason: the repo often sits on a slow 9p/WSL2 bind-mount where the bridge takes ~5s to load — that blows hermes' hardcoded 0.75s MCP-discovery window; the native copy loads in ~0.3s. Re-running `install.sh` refreshes the copy, so **security fixes flow on reinstall** (no longer automatic). Consequence: editing files under `mcp/stdio/` now requires re-running `install.sh` (to re-copy) **and** restarting the client wrapper — not just a wrapper restart.
- **Forward-slash `cwd` on Windows** for Codex agents. The bridge auto-normalizes, but any new Codex thread must be created with a forward-slash cwd or it'll fail `thread/resume` later.
- **Re-register is a full state refresh** for everything except `description`. Tests and dev workflows should assume session state is wiped on re-register — see DECISIONS.md.
- **Skill files live in two places:** `.claude/skills/aify-comms*/` and `.agents/skills/aify-comms*/`. Keep them in sync when editing.
- **The live-status cache is in-memory (`_LIVE_STATE_CACHE`, `service/routers/api_v2.py`), and the service MUST stay single-worker.** As of 2026-06-18 (`97a497a`) the derived agent-status cache is a process-global in-memory dict, not a SQLite table — this resolved the recurring `database is locked` 503s (the old `agent_live_state` table was refresh-written on every dashboard poll). It is only correct with ONE uvicorn process / one event loop, so never add `--workers > 1` without first moving the cache to a shared store (Redis) or sticky routing. The `agent_live_state` table is vestigial (retained for schema compat, read/written by nothing) — don't debug status from a table dump; use `comms_agent_info` / the dashboard. See DECISIONS.md, "Live-status cache is in-memory, not SQLite".
- **Container name is `aify-comms-service`** on the `aify-comms-network` network. Compose project name is driven by `COMPOSE_PROJECT_NAME` in `.env`.
- **No secrets in commits.** `.env` is gitignored; `config/service.json` is generated by `setup.sh`.

## Verify a change actually took effect — `aify-doctor`

**Every deploy path in this repo fails silently.** No error, everything looks installed, and what you changed is not what is running. This bit us repeatedly: a container serving the previous build; `~/.aify-comms` holding new bridge code while every RUNNING wrapper still executes the copy it loaded at boot; an agent registered but with no `AIFY_AGENT_ID` in its process, so its status is dead. **Do not report success from the absence of an error.**

```bash
aify-doctor            # human-readable report
aify-doctor --json     # {ok, checks:[{id, ok, code, detail, fix}]} — for scripted/agent checks
aify-doctor --strict   # exit 1 if any check failed
```

It proves each claim against the running system rather than checking that a file exists:

| check | catches |
|---|---|
| `service` | container serving a build ≠ repo HEAD (a healthy `/health` says nothing about *which* code) |
| `bridge-installed` | `~/.aify-comms` older than the checkout — i.e. you edited `mcp/stdio/` and never re-ran `install.sh` |
| `bridge-running` | **running** bridges started BEFORE the last install → still executing the old code. Names the agents that must restart. |
| `agent-identity` | a REGISTERED agent whose process has no `AIFY_AGENT_ID` (status structurally dead). An unregistered plain session is legitimately id-less and is not flagged. |
| `env-bridge`, `wrappers`, `runtimes` | the plumbing is actually connected |
| `usage-openai` | the ChatGPT quota token works — by calling the API, since an expired token passes a file check |

Operator-facing versions of these flows (install / update integrations / install / update container) are the **Agent playbooks** table in [README.md](README.md).

## Testing a change

```bash
# Backend change (service/ or mcp/sse_server.py)
docker compose up -d --build && curl http://localhost:8800/health

# Bridge change (mcp/stdio/)
# Restart codex-aify or claude-aify in whatever session tests the change.
node --check mcp/stdio/server.js
node --check mcp/stdio/runtimes.js

# Python change
python -c "import ast; ast.parse(open('service/routers/api_v2.py').read())"
```

Full end-to-end test is a two-session live round-trip. Register two agents, use `comms_send` from one to the other, verify the target wakes or receives a steer/queued turn according to capability, and verify the response is threaded back in chat.
