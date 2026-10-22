# Guided install S2: the last review (1792702463380-4c3475c1)

| repo | commit after the reviewed object |
|---|---|
| aify-env 098/s2-chain | 6a2e8fa (C5: bind assets to connect's report of this run; the no-assets skip first) |
| aify-comms 098/s2-chain | e72dd7c3 (C9: an aify-env on PATH must be npm's own bin for the global package), then this evidence |

## First failures (`red/`)

- `env-chain-on-726ddec.tap`: the new tests on the reviewed env, with placeholder exports so the behaviour tests could
  run. Test 28 shows B running through a B-named registry row and a stale row ("B was approved, named in a row and in a
  stale row, but connect installed A; B's package ran"). Test 29 shows a plain approved package with `mcp: []` refused
  instead of skipped. Test 30 has no parser. The other failures are the interface change: connect now returns
  `{status, installed}`.
- `comms-handoff-on-2a9c397f.tap`: a script of somebody else's in npm's prefix accepted as aify-env ("chain, a script of
  somebody else's in the prefix: 0 !== 78").

## Mutants (`mutants/`): 8 of 8 killed

There is a baseline run of both test files first and a restored run after. For each arm the receipts keep the source
sha256 before, mutated and restored (equal), the test sha256, the failing tests and the first assertion. Raw TAP is in
`raw/`.

## Witness (`witness-run7/`)

comms e72dd7c3, env 6a2e8fa, wrapper 3a14911, run 104293f2: exit 0, nine rows pass, and every owned resource reads back
absent by Docker's not-found for its name. On no-early-store's host, aify-env is on PATH from a real `npm install -g` on
Linux. The handoff accepted it as npm's own through the link branch (realpath) and entered the chain, which then refused
at connect, as the row expects.

The Windows cmd-shim branch was checked read-only against this host's real npm shim, which passed; a foreign file was
refused. The Unix link branch runs under a fake `realpath` in the unit tests, because this Windows host cannot create
symlinks.
