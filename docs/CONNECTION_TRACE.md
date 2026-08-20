# Does it all connect? Traced, 2026-08-20

Three repos that have to agree with each other, none of which can import the others. This records every
link between them and what proves it — because "each half is well tested" is exactly the state in which
two halves quietly disagree.

**The failure this exists to catch has a shape.** Every defect in this work was invisible for the same
reason: the tests supplied what production would have had to supply. A client tested against a fake
server and a server tested against a fake client are both green while the pair is broken.

## The links

| # | From → To | What crosses | Evidence |
|---|---|---|---|
| 1 | aify-comms → registry file | its own service entry | `register-service-cli.test.js` |
| 2 | registry → aify-wrapper | endpoints, `endpointEnv`, strict opt-in | fixture test in aify-wrapper: real writer output, parsed by the authoritative parser |
| 3 | registry → aify-wrapper → launcher | a fingerprint of what it was built from | `install-chain-across-three-repos.test.js` |
| 4 | launcher → `aify-wrapper-check` | is it still current | same test: install, drift, **stale**, reinstall, **healed** |
| 5 | registry → aify-env | which services exist | fixture test in aify-env |
| 6 | aify-comms client → aify-env server | start / list / health / output / stop / refusal | `env-client-against-real-aify-env.test.js`, over a real socket |
| 7 | aify-env doctor → aify-comms | `/health` | **verified live** — see below |

## Link 7, verified against the running service

Read-only, and the documented health check. aify-env's doctor, pointed at a registry naming the real
aify-comms:

```
  ok   terminal       a real terminal is available for processes that need one
  ok   registry       1 service(s) registered: aify-comms
 FAIL  environment    no environment is running at http://127.0.0.2:1
  ??   processes      no environment answered, so what it owns is unknown
  ok   aify-comms     http://localhost:8800 reports healthy
```

Three things worth noticing in that output rather than only the last line. The **shapes agree**:
aify-comms answers `{"status":"healthy", …}` and aify-env's `probeService` requires a string `status`,
so a service that answered with HTML or a bare 200 would have read `unanswered` instead. All **three
states appear in one real run** — passed, failed, unanswered — which is the design working rather than
a claim about it. And the health body carries **no credential**: the ntfy block is counters and an
`enabled` flag, never the topic, which is the one value in this project that must never appear in a
response.

## What the trace corrected

**Two earlier attempts at link 3 were vacuous.** Run by hand through a shell, `/tmp/x` reached node as
`C:\tmp\x`, which does not exist — so the registry was absent at every hop, every fingerprint was the
empty-registry digest, and "1 current" meant two nothings agreeing. It looked exactly like success. The
test now asserts the fingerprint is **not** the empty one before believing anything else.

**Link 6 did not exist as a test at all** until this pass. Both sides had thorough suites and had never
met.

## What is still NOT connected, and deliberately

**aify-comms does not delegate spawns to aify-env.** The seam is wired and refuses when enabled; see
[PHASE8_STATUS.md](PHASE8_STATUS.md). The blocker is not effort: aify-comms composes a shell command
STRING and aify-env allowlists a launcher FILE, and bridging those is a decision about the
service-to-bridge contract rather than an edit.

## Re-running this

Links 1–6 are tests; run the suites. Link 7 needs a running aify-comms and is a read-only GET:

```bash
node -e "require('fs').writeFileSync('/tmp/r.json',JSON.stringify({version:1,services:{'aify-comms':{endpoint:'http://localhost:8800',endpointEnv:['AIFY_SERVER_URL'],mcp:[]}}}))"
AIFY_SERVICE_REGISTRY=/tmp/r.json aify-doctor
```
