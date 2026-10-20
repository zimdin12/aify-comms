# S1: a versioned spec, its trust boundary, and a connect that never replaces a working entry

Part of [the guided-install plan](../2026-10-20-guided-install.md). Answers R1 and R2.

## The spec is metadata, never authority

`GET <endpoint>/.well-known/aify-service` serves a typed document: `specVersion: 1`, `name`, `fixed`, `mcp`,
`secrets` and `verify`. A version that is unknown, or missing, is refused.

**Identity first.** Before connect reads, writes or uses any credential:
- the spec's `name` must equal the service name the operator selected;
- the spec must come from the operator's selected endpoint, with redirects refused (`redirect: "manual"`, as the
  doctor's probe does, mcp/stdio/doctor.js:512-527).

**Routes stay on that authority.** Every path in `fixed` and `verify` must pass the existing service-relative route
rule: absolute, no scheme or host, no `//`, no backslash or control character (aify-wrapper lib/registry.mjs:185-200,
215-223). A credential-bearing request goes only to `<selected endpoint><path>`, with redirects refused. A spec that
names another host, or a route that redirects, is refused before any secret is read.

**Code needs approval the spec cannot give.**
- The spec may name a client artifact only as `{source, revision, bin}`: a git-form package, an immutable commit,
  and a bin that package's own `package.json` declares.
- An artifact is approved one of two ways:
  - it matches the trusted catalogue aify-wrapper ships (reviewed source, revision and bin per service);
  - or the operator approves that exact triple in the plan the installer prints.
- Under `--no-prompt`, a missing approval fails.
- The bin is resolved from the installed package's own directory, never looked up on PATH.
- A spec field cannot supply a command or args outside the approved package.

## Fields the parser ignores keep their owner's validation

- `parseRegistry` validates the fields it normalises and drops the others (lib/registry.mjs:131-142). It checks the
  cross-service collision only for `credentialRef` and `agentState.credentialRef` (:111-128).
- aify-env reads every field matching `isCredentialRefField` (credential-store.mjs:109; services.mjs:18-23,
  :118-121, :159-164) and reads `advertise` as written, through `advertisingChoice` (advertise.mjs:344).
- So connect checks a candidate registry in two places, and both must accept it:
  - `parseRegistry` for the fields aify-wrapper owns;
  - a new `aify-env registry check <file>` for the fields aify-env owns: each `*CredentialRef` by the store's grammar,
    case-folded ownership across every service's credential references, and `advertise` by `advertisingChoice`.
- Connect writes the raw merged entry: the spec's fields over the existing entry, keeping every field already
  published that the spec does not name (legacy fields included).
- A spec field that no owner validates is refused, never written as a validated instruction.

## Order, and what a failure leaves

1. **Prepare.** Fetch the spec and check identity, routes and approvals. Resolve the approved artifacts; they must
   already be acquired (S2, phase 1).
2. **Stage.** Store each new secret under a fresh staged reference: `aify-env credential set --service <name>
   --stdin --ref <name>-staged-<nonce>`. An in-use reference is never overwritten.
3. **Probe** with the staged key:
   - `/health` returns 200;
   - the authenticated route returns 200 with the key, and 401 both without it and with a wrong one.
4. **Validate.** Build the candidate registry, pointing at the staged references, and run both checks.
5. **Publish.** Replace the registry atomically, only if the file still hashes to what step 1 read. Otherwise abort:
   a concurrent change is reported, never overwritten.
6. **Retire.** Remove the old references only after publish.

- A failure at steps 1 to 5 removes only the staged references. The registry bytes, the old references and the
  launchers stay as they were.
- If rendering fails after publish, the registry is new and the launchers are stale. `aify-wrapper-check` reports
  that state, and a re-render repairs it.

## comms' and the dashboard's specs

- **comms** (stdio until S4):
  - `fixed`: `agentState {path: "/api/v1/agent-state"}`, `endpointEnv` and `keyEnv`.
  - `mcp`: both of today's servers, `aify-comms` → `server.js` and `aify-comms-channel` → `claude-channel.js`, as
    files of the declared artifact "comms bridge copy" at `~/.aify-comms/mcp/stdio`. comms' installer acquires that
    artifact in phase 1 (today's `copy_bridge_to_native_dir`).
  - `secrets`: `credentialRef` = API_KEY.
  - `verify`: `/api/v1/agents`.
- **The dashboard**:
  - `fixed`: the fields dashboard-manager gave.
  - `secrets`: `credentialRef`, plus `secretsCredentialRef` (optional).
  - `verify`: `/health` returns 200; `/api/v1/settings/schema` returns 200 with the key and 401 without or with a
    wrong one (reported PROVEN by dashboard-manager).
  - `mcp`: **PENDING.** The package bin and the host-fact descriptor (machine id, cwd, `AIFY_*` env, credential
    file) are still to come. The package's actual content is inspected before anything calls it client-only.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| approved control | connect to a fixture serving comms' spec writes an entry byte-equal to today's `register-service-cli` stdio output, both servers included | the entry differs |
| identity and authority | specs with a different `name`, a cross-host route or a redirecting route are refused, and a credential-store spy records zero reads or writes | any secret is touched |
| code authority | a spec naming an unapproved source or revision, or a bin outside the package, is refused under `--no-prompt`, and nothing executes | it installs or runs |
| owner validation | a `secretsCredentialRef` that case-folds onto another service's `credentialRef` is refused | it is written |
| raw preservation | a reconnect keeps `secretsCredentialRef` and `advertise` byte-equal | the parser's projection is written |
| no replacement on failure | a reconnect with a wrong key, or a final probe answering 200 without a key, leaves the registry bytes, the old references and the launcher fingerprint unchanged | any of them moves |
| concurrent change | the registry is changed between prepare and publish, and connect aborts | the change is overwritten |
