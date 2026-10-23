# Guided install S2: connect's record and npm's shim bytes (preliminary 1792718055661-ceb1f544)

| repo | commit after the reviewed object |
|---|---|
| aify-wrapper 098/connect-report (from main 1070099) | 0e05ed8: connect takes `--report-file <path>` and writes its report's `installed` there as JSON, before the printed report |
| aify-env 098/s2-chain | a97ffc9 (on 6a2e8fa): C5, the package binding reads connect's record, never its printed report |
| aify-comms 098/s2-chain | 474e2652 (on aefca598): C9, a shim on PATH is npm's only if byte-equal to npm's cmd-shim output; then this evidence |

The wrapper is a third repository in this change. The chain refuses a wrapper whose connect does not accept
`--report-file` and prints the update guidance. Probed on the real parser: main 1070099 answers
`unknown option --report-file`, and 0e05ed8 answers `""`.

## First failures (`red/`)

- `wrapper-connect-package-on-1070099.tap`: `--report-file` is an unknown option on wrapper main.
- `env-chain-on-6a2e8fa.tap`: the new tests on the reviewed env, with a placeholder `connectRecorded` export.
  - Test 7: "a wrapper whose connect cannot write its record passed".
  - Test 30: no record is read.
- `comms-handoff-on-aefca598.tap`: "chain, a script that names the package's bin only in a comment: 0 !== 78".

The forged case is in the wrapper test. A pending reason `deferred\n  bridge-b: installed at /forged/b` prints a
line that reads as an install, while the record lists only `dashboard-bridge`. The env test does the same: a fake
connect prints a forged `bridge-b` line, and the receipt holds only the record's row.

## Green (`green/`)

| repo | result |
|---|---|
| env chain | 30/30 |
| comms handoff | 15/15 |
| wrapper | connect-package 8/8 |

Focused tests only. Also green: env install-entry plus one-command (10), and comms register-service-cli-credential plus
no-unwatched-oversized-file (13).

## Mutants (`mutants/`): 13 of 13 killed

The arms are in `arms.py.txt`.

- `run1/` ran every arm. 12 were killed. "the shim's root never compared with npm's" survived. The cause was the
  fixture: the other root held no cmd-shim, so the missing cmd-shim refused that case before the root check could.
  The other root now holds cmd-shim and a bin with a shebang, so only the root comparison can refuse it.
- `run2-handoff/` reran all 7 handoff arms against that test. All 7 were killed, the root arm on "a shim in the prefix
  whose node_modules is not npm's root".

Each run has baseline and restored runs, sha256 before, mutated and restored (equal), and raw TAP.

## Host check, read-only (the Windows branch on the real npm)

npm's cmd-shim (npm 10.9.3, `$root/npm/node_modules/cmd-shim` 7.0.0), run for the package bin's first line in a
private copy of the prefix layout, writes a shim byte-equal to this host's real `C:\nvm4w\nodejs\aify-env` (`cmp` 0).
A file holding the target string in a comment differs (`cmp` 1). The check leaves no temp directory behind; none was
newer than the run.

## Witness (`witness-run8/`)

comms 474e2652, env a97ffc9, wrapper 0e05ed8, run 8f2ff819: exit 0, all nine rows pass, and every owned resource read
back absent. Connect committed through the chain on no-recursion and render-after, so the wrapper's `--report-file` and
the chain's capability check met for real. On Linux the handoff accepted npm's real global link through the link
branch, as in run 7.
