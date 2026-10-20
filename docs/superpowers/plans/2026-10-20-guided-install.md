# Guided install: any piece installs and connects the rest (2026-10-20, plan, not built)

The operator, 2026-10-20: "i go to new pc, i tell agent to install aify-comms or aify-env and aify asks stuff if
needed and installs env and wrapper etc also. or if i ask to install aify-env and connect it with comms in xxx and
dashboard in yyy." For review by comms-senior-dev, and by dashboard-manager for the spec format, before any code.

## Today (aify-comms 7c1f76da, aify-env 2ae603de, aify-wrapper 5a080a1)

- **Three installers, none chains another.**
  - comms `install.sh --client` installs its bridge copy and assets (install.sh:1948-2010).
  - It never installs aify-env or aify-wrapper: no `npm install -g` or `aify-wrapper-install` anywhere in it.
  - aify-wrapper arrives only as a pinned npm dependency, whose templates comms renders (mcp/stdio/package.json:26).
  - aify-env's installer installs itself, then asks for credentials, but only for services **already** registered
    (aify-env install.sh:62-83, scripts/install-credentials.mjs:27-35,96-125).
  - aify-wrapper's installer needs `--endpoint` and defaults the bridge to `~/.aify-comms/mcp/stdio`
    (aify-wrapper install.sh:12-22).
- **Each service writes its own registry entry.**
  - comms: `register-service-cli.mjs:42-63`, through `upsertService` (mcp/stdio/service-registry.mjs:47-132), which
    imports aify-wrapper's `parseRegistry` so it never writes what the reader refuses (:27-31, :93-101).
  - The dashboard: its own `bin/register.mjs` (dashboard-manager; not read here).
- **The client path still carries comms code.**
  - The bridge is copied to `~/.aify-comms` (install.sh:279-300, :1959).
  - The comms channel server runs from that copy (register-service-cli.mjs:63).
  - `--mcp-transport sse` avoids the copy (install.sh:67-69), but 5 tools are unmirrored (TARGET_ARCHITECTURE gap 1).
- **An agent-facing skill exists:** `aify-comms-install` (inspect, ask the gaps, plan, apply, verify), backed by
  `scripts/install-state.sh` and `scripts/components.sh`.

## The design

1. **Any installer is an entry point; one chain, in a fixed order.**
   1. aify-env.
   2. aify-wrapper.
   3. Connect each named service.
   4. Render launchers.
   5. Each service's client assets (skills, MCP registration, hooks), per runtime found on PATH.
   6. Doctors.

   - Launchers render **after** every connect, because a launcher bakes the registry fingerprint (aify-wrapper
     install.sh:180-188; comms install.sh:1965-1967 states the same order).
   - aify-env and aify-wrapper install in the git form, since neither is on npm (TARGET_ARCHITECTURE:35-38).
   - The chain is written once, in aify-env (`install.sh --connect <service>=<url> ... --client <runtime>...`).
     comms' and the dashboard's installers, run on a host missing aify-env, install aify-env and hand it their own
     connect.
2. **A declarative spec per service, served by the service.**
   - Served at `GET <url>/.well-known/aify-service`, unauthenticated and secret-free: a new host with no checkout can
     only learn a service by asking it.
   - Fields:
     - `name`;
     - `fixed` (registry fields copied as given);
     - `mcp` (`{http: {path}}`, or `{package, bin, args}`, which connect resolves to the installed bin's absolute path);
     - `secrets` (`[{field, label, required}]`);
     - `verify` (`health`, plus one authenticated route that must answer 200 with the key and 401 without it and
       with a wrong one).
   - **The connect step lives in aify-wrapper** (`aify-wrapper connect <name> <url>`), because:
     - the authoritative parser is there (lib/registry.mjs:39-135);
     - re-rendering launchers after a write is its job;
     - aify-env deliberately reads only a subset of the registry (lib/services.mjs:1-6).

     comms' `upsertService` moves there, and comms and the dashboard stop writing entries themselves.
   - Secrets go through the store's own command, `aify-env credential set --service <name> --stdin [--ref <name>]`
     (bin/aify-env-credential.mjs:47), as aify-env's installer already does (install-credentials.mjs:83-94).
   - Allowed fields are whatever `parseRegistry` accepts: connect builds the entry, parses it, and refuses on error.
     It keeps no field list of its own.
   - **The parser is a gate, never the writer.** `parseRegistry` rebuilds each entry from named normalisers
     (lib/registry.mjs:131-142), so its output drops fields it does not normalise, such as `secretsCredentialRef`
     and `advertise`, which aify-env reads. Connect writes the raw merged entry: the spec's fields over the existing
     entry, keeping every field already published that the spec does not name. That is the rule dashboard caab988
     adopted after its register lost two fields.
   - **comms' spec, from its live entry:**
     - `fixed`: `agentState {path: "/api/v1/agent-state"}`, `endpointEnv` and `keyEnv` (aify-service-endpoint.mjs).
     - `mcp`: `aify-comms` over `{http: {path: "/mcp/sse"}}` from S3. Until S3 the spec also declares today's stdio
       form, and connect writes that one.
     - `secrets`: `credentialRef` = API_KEY, required.
     - `verify`: `/health`, then `/api/v1/agents` 401 without a key and 200 with it, as doctor's `client-api-key`
       probes (mcp/stdio/doctor.js:512-527).
   - **The dashboard's spec, verbatim from dashboard-manager:**
     - `fixed`: `agentState {path: "/api/v1/agent-state"}`, `sessionInject {mcp: true}`, `advertise: false`,
       `turnStart {path: "/events/turn-start", budgetMs: 800}`.
     - `mcp`: the git-form package `github:zimdin12/aify-dashboard`. Its bin name is still to come, and the bridge
       needs host facts.
     - `secrets`: `credentialRef` = API_KEY, required; `secretsCredentialRef` = SECRETS_FETCH_KEY, optional.
     - `verify`: `/health` 200 unauthenticated; `/api/v1/settings/schema` 200 with the key, 401 without one and 401
       with a wrong one (dashboard-manager reports it PROVEN live on :9700).
     - Never `keyEnv` or `endpointEnv`.
3. **Backend vs client.**
   - On a host that only runs agents, comms is connected over SSE, so `~/.aify-comms` is never created.
   - Mirroring the 5 unmirrored tools is ordinary work (TARGET_ARCHITECTURE gap 1). Until that lands, a host that
     needs them installs the stdio copy explicitly, and install-state reports it.
   - The comms notify hook (`notify-check.js`) also runs from the copy. It moves to aify-wrapper or is dropped,
     decided in that slice.
   - A service may declare a client **package** (as the dashboard's bridge does). That is not service code: the
     backend never lands on the host.
4. **The channel is aify-wrapper's plugin.**
   - aify-wrapper ships one Claude Code channel plugin. The comms run claim and wake semantics stay in comms.
   - Branch A (allowlist works; the operator-approved experiment decides): the install asks for admin consent once,
     writes `channelsEnabled` + `allowedChannelPlugins` to managed settings, and the launcher renders
     `--channels plugin:...`.
   - Branch B (it does not): the launcher keeps `--dangerously-load-development-channels`. herdr-aify and aify-env
     press Enter on that warning's exact text, only in panes they launched; a non-match waits as today.
   - The branch is a fact detected at render time (setting present and plugin installed), not a flag.
5. **Questions come from the skill; scripts take flags.**
   - One guide, `aify-env/docs/INSTALL.md`, is linked from all three READMEs. An agent on a new PC reads it from
     GitHub; once installed, the `aify-comms-install` skill (renamed `aify-install`) carries it.
   - It asks only for gaps: role (service host / agent host / both), runtimes, each service URL, each secret
     (hidden), optional herdr, and admin consent for branch A.
   - Scripts are non-interactive with `--no-prompt`. Secrets come on stdin or a named file, never argv.
6. **Done** means `aify-env doctor`, `aify-wrapper-check` and each connected service's doctor are green.
   - It is tested in dashboard-manager's disposable Linux container (their task #17).
   - The container registers to live comms under a machine id naming it as a test, and is removed afterwards by
     the normal remove.

## Slices, each shippable

| # | slice | witness | RED when |
|---|---|---|---|
| S1 | specs served by comms and the dashboard; `aify-wrapper connect` writes the entry, stores secrets, verifies, re-renders | connect to a fixture service yields an entry byte-equal to today's `register-service-cli` output for comms (stdio form); a re-connect keeps a field the spec does not name (`secretsCredentialRef`) | the fixture answers 200 without a key and connect still succeeds; an entry the parser refuses is written; or the parser's output is written and drops a field |
| S2 | the chain in aify-env's installer; comms' and the dashboard's installers hand off to it | node+git-only container: `install.sh --connect aify-comms=<fixture> --client claude --no-prompt`, then `aify-wrapper-check` current | launchers are rendered before connect (the check reports the fingerprint stale) |
| S3 | comms over SSE on agent hosts; the 5 tools mirrored | `transport-parity.test.js` declares no unprincipled gap; install-state reports no bridge copy on a client-only host | the client-only install copies `~/.aify-comms` |
| S4 | the channel plugin, branch A or B | a relaunched resident reaches its first channel wake with no keystroke from the operator | the launcher renders the dev flag while the allowlist is present (A), or the Enter is sent on non-matching text (B) |
| S5 | guide + skill; live run in task #17's container | that container ends with every doctor green and is removed | any question is asked whose answer was already on the host |

## ASSUMED, not verified here

- The `.well-known/aify-service` route exists nowhere yet; it is proposed.
- Branch A on a personal account: unproven until the operator-approved experiment.
- That `aify-env credential set --ref` accepts the dashboard's `secretsCredentialRef` name.
- Linux-container behaviour of all three installers.
