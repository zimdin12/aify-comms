---
name: aify-comms-install
description: Use when asked to install, set up, update, upgrade, reinstall, or connect a machine to aify-comms — including adding a coding-agent client, changing the endpoint, or turning on the API key.
---

# Installing and updating aify-comms

**Ask the machine before asking the person.** Most of what an installer needs is a fact about this
host, and one command reads all of it:

```bash
bash scripts/install-state.sh
```

Run it first, every time. Put only the gaps to the operator.

## Two installs, and this machine may want either

| they want | this machine runs |
|---|---|
| the service — database, dashboard, the API agents talk to | the container |
| to run agents here | the launchers + `aify-env` |

Both on one machine is normal. The service can also live on another host, in which case this one
installs only the client side and points at that URL.

## Installing

**1. The service, if this machine hosts it.** Skip when `container` is already `running`, or when the
service lives elsewhere.

```bash
./setup.sh                        # generates .env + config from the examples
docker compose up -d --build      # API :8800, Dashboard Next :8801
curl http://localhost:8800/health # {"status":"healthy"}
```

**2. The agent side, once per coding-agent client.** `install-state.sh` lists what is already there
under `launchers`; install the ones that are missing.

```bash
bash install.sh --client claude  <endpoint> --with-hook
bash install.sh --client codex   <endpoint> --with-hook
bash install.sh --client hermes  <endpoint> --with-hook
```

`<endpoint>` is the service URL — `http://localhost:8800` on the machine hosting it, the LAN address
otherwise. `install-state.sh` reports the endpoint already installed; reuse it unless the operator is
moving the service.

**3. The environment tier, if agents run here.** Managed spawns are delegated to it and fail loudly
without it:

```bash
npm install -g github:zimdin12/aify-env
aify-env            # foreground; run it as a service if this host should always be spawnable
```

**4. Verify, and say what must be restarted.**

```bash
aify-comms doctor
```

Every deploy path in this repo fails silently, so absence of an error is not success. `doctor` proves
each claim against the running system. Read [../aify-comms/references/operations.md](../aify-comms/references/operations.md) for what each check means.

## Updating

```bash
git pull
bash scripts/install-state.sh     # what this machine has now
```

Then apply only what changed, using `git diff --stat HEAD@{1}` to see which:

| changed under | do |
|---|---|
| `service/`, `mcp/sse_server.py`, `config/` | `docker compose up -d --build` |
| `mcp/stdio/` | re-run `install.sh` for each client, then relaunch every wrapper |
| `.claude/skills/` | re-run `install.sh` — skills are COPIED out, editing them changes nothing until then |
| docs only | nothing |

Finish with `aify-comms doctor`. `bridge-current` reading red after a bridge update is accurate, not a
false alarm: it means wrappers are still running the code they loaded at boot. It clears when they
relaunch.

## What to actually ask the operator

Ask only what the state report cannot answer:

1. **Does this machine host the service, run agents, or both?** Everything else follows.
2. **Which coding-agent clients?** Only if `launchers` is missing ones they use. claude, codex and
   hermes are supported; OpenCode and Pi installs are deliberately disabled.
3. **Which endpoint**, only when none is installed and the service is not local.
4. **Turn on the API key?** Only if `apiKey` is `none` and they want the service to stop accepting
   unauthenticated calls. It is `bash install.sh --client <c> <endpoint> --with-api-key`, then
   `docker compose up -d`, then open the dashboard once at `<endpoint>/?api_key=<the value in .env>`.
   An existing key is reused, never rotated.

Do not ask about notification hooks; `--with-hook` is the right default. Do not ask about paths,
ports, or wrapper flags — the defaults are correct and the report names any that are not.

## Two things that will bite

**Never run a bare `aify-comms` to check whether something works.** It starts the environment bridge,
supersedes the one already serving this host, and its managed workers are reaped. Use
`aify-comms --check` (validates and registers nothing) or `aify-comms doctor`.

**Editing `mcp/stdio/` or `.claude/skills/` changes nothing until `install.sh` runs again.** Both are
COPIED to `~/.aify-comms` and `~/.claude/skills`; the checkout is not what executes.

## When to read more

Per-runtime detail — wrapper internals, session handling, permissions — is in
[../../../install.claude.md](../../../install.claude.md), `install.codex.md` and `install.hermes.md`.
Reach for one only when a client behaves oddly after a correct install.
