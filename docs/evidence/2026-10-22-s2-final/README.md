# Guided install S2: the final-verdict round (1792696826105-fa81c862)

Successor of the reviewed env 0931f884 and comms 5ca78a61, history kept:

| repo | commits after the reviewed object |
|---|---|
| aify-env 098/s2-chain | 726ddec (S2R3, S2R4, S2R5) |
| aify-comms 098/s2-chain | 816b176d (preliminary: write-before-refusal, npm root unknown), 2a9c397f (S2R1 proof, S2R2, E1, E3), then this evidence |

## First failures (`red/`)

| receipt | object | what failed |
|---|---|---|
| `comms-handoff-on-5ca78a61.tap` | the reviewed comms | S2R1 through the installer with the real resolver: `.env` written (`API_KEY=...`) before the 64 refusal; S2R2 root unknown and split PATH |
| `comms-handoff-on-816b176d.tap` | the preliminary fix | S2R2 split PATH only: S2R1 and the unknown root are closed there |
| `env-chain-on-0931f88.tap` | the reviewed env | S2R5 a wrapper without validateSpec passed; S2R3 B's unapproved package ran |
| `env-entry-on-0931f88.tap` | the reviewed env | S2R4 an empty key path was given the caller's directory |
| `readback-on-the-loose-rule.tap` | the classifier with the predecessor's rule | E3 a not-found about another name counted as absent |

## Mutants (`mutants/`)

`mutants.txt` (summary) and `mutants.json` (everything): a baseline run of each of the four test files on the unmutated
tree, then 13 arms, each with its source's sha256 before, mutated and restored, the test file's sha256, the failing
tests and the first assertion's own words, then a restored run of all four. Raw TAP for every run is in `raw/`.
13 of 13 killed, each restored hash-equal; baseline and restored both green. The arms include E2's production route
(assets for every client on PATH) and narrow complements of the earlier arms 2 and 13, each first failing on the
assertion it names.

## Focused receipts (`focused/`)

Run at comms 2a9c397f and env 726ddec (`comms-HEAD.txt`, `env-HEAD.txt`), exits in `exits.txt`: the standalone
registration CLI (`comms-register-service-cli-credential.tap`, 8/8), the size ceiling, the child-environment seal,
the hermes and credential-carrier tests, 12 python installer/redeploy/hook/endpoint files (96 passed), the docs and
boundary gates (146), and the four env gates. No full suite, as directed.

## Witness (`witness-run6/`)

comms 2a9c397f, env 726ddec, wrapper 3a14911, run c6689750: exit 0, nine rows pass. `cleanup.json` holds every
removal and read-back: command, exit, output and classification; each read-back is absent by Docker's own not-found
for that name. Entry rows start on cold hosts; the rows after an entry read that host, and no-early-store's host is
prepared on purpose (the witness header says which is which).

## Not covered

Native Windows, real codex and hermes CLIs, a real host aify-env, a package service through this driver, and connect's
own refusal preserving a prior reference. The runtime CLIs here are logging stubs and the package sources are local.
