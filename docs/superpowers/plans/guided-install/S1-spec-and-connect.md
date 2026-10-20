# S1: a versioned spec, typed artifacts, and a connect that never replaces a working entry

Part of [the guided-install plan](../2026-10-20-guided-install.md). Rev 3 answers REVISE 03375b9b, items 1-3.

## The spec is metadata, never authority

`GET <endpoint>/.well-known/aify-service` serves a typed document: `specVersion: 1`, `name`, `fixed`, `artifacts`,
`mcp`, `secrets` and `verify`. A version that is unknown, or missing, is refused.

**URLs.** Every route is an absolute path, resolved **origin-relative**: `new URL(path, endpoint)`. An endpoint that
carries a path (`http://h/comms`) resolves `/api/v1/agents` to `http://h/api/v1/agents`, never by concatenation.
Each path must pass the existing route rule: absolute, no scheme or host, no `//`, no backslash or control
character (aify-wrapper lib/registry.mjs:185-200, 215-223).

**Before any credential is read or written:**
- the spec is fetched with no key and `redirect: "manual"`. A redirect at this stage is refused;
- its `name` must equal the service the operator selected;
- every route must resolve to the selected origin;
- every artifact must be approved (below).

## Typed artifacts

An artifact is `{id, kind, source, sha256, entries}`. Every MCP server names an artifact and an entry, never a
command.

- **`kind: package`.** An npm package. `entries` names bins its own `package.json` declares.
- **`kind: file-set`.** A bounded legacy copy. `sha256` binds a manifest of relative paths and their hashes, and
  `entries` names files in that manifest.
- **`source`**:
  - `{git: {repo, revision}}`: an immutable commit;
  - or `{served: {path, version}}`: the service serves the tarball, fetched from the selected origin with the
    service key, with `sha256` checked before unpacking.
- **Install root.** Derived per kind, never taken from the spec:
  - a package goes to npm's global root;
  - comms' file-set goes to `$AIFY_HOME/mcp/stdio`, which defaults to `~/.aify-comms` (install.sh:28).
- **Render.** One rule for both kinds: `{command: "node", args: [<install root>/<entry file>]}`. For a package bin
  the entry file is the file its `bin` maps to (the Windows-safe form dashboard-manager proposed; ASSUMED on
  Windows, not run).
- **Approval.** An artifact is approved one of two ways:
  - its `{kind, source, sha256, entries}` matches the catalogue aify-wrapper ships;
  - or the operator approves that exact tuple in the printed plan.
- Under `--no-prompt`, a missing approval fails. Nothing outside an approved artifact is executed or resolved
  from PATH.
- **Where the dashboard's package comes from is PENDING the operator** (dashboard question 01M5CYJH):
  - the aify-dashboard repo is private, so `git` fails on a fresh host;
  - the recommended option is `served`;
  - both fit this schema.

comms' S1 spec, which renders byte-equal to today's `register-service-cli.mjs:61-64`:

```
artifacts: [{id: "comms-bridge", kind: "file-set", source: {git: {repo: "zimdin12/aify-comms", revision: <sha>}},
             sha256: <manifest hash of mcp/stdio at that revision>, entries: ["server.js", "claude-channel.js"]}]
mcp: [{name: "aify-comms", artifact: "comms-bridge", entry: "server.js"},
      {name: "aify-comms-channel", artifact: "comms-bridge", entry: "claude-channel.js"}]
```

A `package` entry could not represent this: `mcp/stdio/package.json` declares only `aify-comms-mcp -> ./server.js`.
The dashboard's spec uses `kind: package`. Its source is PENDING; its entry is the bin `aify-dashboard-bridge`,
reported PROVEN on Linux by dashboard-manager.

## Fields the parser ignores keep their owner's validation

- `parseRegistry` validates the fields it normalises and checks cross-service collisions only for `credentialRef`
  and `agentState.credentialRef` (lib/registry.mjs:111-142).
- aify-env reads every `isCredentialRefField` field (credential-store.mjs:109) and `advertise` as written
  (advertise.mjs:344).
- So a candidate must pass two checks:
  - `parseRegistry`;
  - a proposed `aify-env registry check <file>`: each credential reference by the store's grammar, case-folded
    ownership across every service, and `advertise` by `advertisingChoice`.
- Connect writes the raw merged entry, keeping fields already published. A spec field that no owner validates is
  refused.

## Order, the commit boundary, and what a failure leaves

1. **Prepare** (no credential I/O): the checks above.
2. **Stage.** Only when a new key is supplied: `aify-env credential set --service <name> --stdin --ref
   <name>-staged-<nonce>`. A reconnect with no new key reuses the published reference and stages nothing.
3. **Probe** with the key:
   - `/health` returns 200;
   - the authenticated route returns 200 with the key, and 401 both without it and with a wrong one.

   Every request is `redirect: "manual"`. A 3xx at any point, including one that answers only the correct key,
   aborts. The redirect is never followed and the key is never sent to its `Location`. `served` artifacts are
   fetched here, into a staging directory, and hash-checked.
4. **Validate** the candidate registry, pointing at the staged references.
5. **Publish under the registry lock.**
   - Acquire `~/.aify/services.json.lock`, created exclusively (`open(..., "wx")`, holding pid and start time).
   - Holding it: reread the registry, check it still hashes to step 1's read, write a temp file, rename it over the
     registry, then read it back.
   - Release the lock.
   - A held lock waits a bounded time, then fails naming its holder. It is never stolen.
   - After S1, connect is the only cooperating writer: comms' `register-service-cli` is retired, and the dashboard's
     register is asked to call connect. A writer that ignores the lock is named as such until it moves.
6. **Classify** from the readback:
   - **not committed**: failed before the rename;
   - **committed**: the readback hash equals the candidate's;
   - **unknown**: anything else.
7. **Clean up.** Staged references are removed only when the result is **not committed**. On committed or unknown
   they are kept (active, or possibly active), and unknown is reported.
8. **Old references are never removed by connect.** Each one is reported as `cleanup-pending`, with the command
   to remove it once the operator confirms no consumer still holds it. A retained raw field or a client running
   old config may still name it.

If rendering fails after a committed publish, `aify-wrapper-check` reports the launchers stale, and a re-render
repairs them.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| approved control | a fixture serving comms' S1 spec yields an entry byte-equal to today's dual-server `register-service-cli` output | the entry differs |
| metadata authority | a spec with another `name`, a cross-origin route, or a redirect on the unauthenticated fetch is refused, and the credential-store spy records zero reads or writes | any credential is touched |
| auth-dependent redirect | a route answering 401 without a key and with a wrong key, and 302 only with the right key: connect aborts; the fixture at `Location` receives zero requests; registry, references and launchers are unchanged | it follows, forwards or publishes |
| origin-relative URLs | endpoint `http://h/comms`: the probe requests `http://h/api/v1/agents` | `http://h/comms/api/...` is requested |
| typed artifacts | an artifact with an unapproved source or hash, an entry not in its manifest or `bin`, or a `command` field is refused under `--no-prompt`, and nothing executes | it installs or runs |
| owner validation | a `secretsCredentialRef` that case-folds onto another service's `credentialRef` is refused | it is written |
| raw preservation | a reconnect keeps `secretsCredentialRef` and `advertise` byte-equal | the parser's projection is written |
| no replacement on failure | a wrong key, or a probe answering 200 without a key: registry bytes, old references and the launcher fingerprint are unchanged, and the staged reference is gone | any of them moves |
| writer exclusion | a second writer holding the lock blocks publish, which fails naming it. A write landing between prepare and the locked reread aborts | the registry is overwritten |
| commit boundary | rename succeeds and the readback is made to fail: the staged references are kept and the result is reported unknown | they are deleted |
| retirement | after a key change the old reference still exists and is reported `cleanup-pending` | connect removes it |

## Format freeze: the format owner's review (dashboard-manager, 2026-10-20)

These amend the clauses above. Where they differ, these win.

1. **Render uses an absolute node.** `command` is the absolute path of the node running connect (`process.execPath`), recorded in the plan the operator approves. A bare `node` would resolve from each session's PATH, contradicting "nothing resolved from PATH". This replaces `{command: "node", ...}` in **Render**.
2. **Secrets are typed.** `secrets: [{field, required, probe?}]`.
   - A required secret with a probe is verified as **Verify** defines.
   - An optional secret that is absent is a working state, not an error.
   - A secret with no probe is reported `stored, not probed`, never silently passed.
   - The dashboard: `credentialRef` (API_KEY, required, probed); `secretsCredentialRef` (SECRETS_FETCH_KEY, optional, no probe yet).
3. **An unavailable artifact has one representation.** `source: {pending: "<reason>"}`. Connect writes no `mcp` entry for it, reports the reason, and refuses `pending` combined with any `sha256`, revision or URL. The dashboard ships its artifact `pending` until operator question 01M5CYJH is answered.
4. **Session identity stays in the session's environment.** No `AIFY_*` value and no key value ever appears in a rendered entry or in the spec. Witness: render an entry with every `AIFY_*` variable and a planted key set in connect's own environment, then assert that none of their values appears in the written entry.
5. **`served` artifacts are fetched with the service key.** The spec route itself stays unauthenticated and secret-free (names, paths, hashes).
6. **The dashboard's register is a non-cooperating writer** (temp file and rename, no lock) until it calls connect or takes the same lock, as step 5 requires naming.

Corrected attribution: dashboard-manager's sandbox PROVED that the installed bin serves a full MCP session when spawned directly. It did not render or read a services.json `mcp` entry, so the entry form (bare bin, or node + file) is ASSUMED on Linux as well as Windows. Their sandbox adds that check once connect renders an entry.
