# Guided install S2: the review round (1792691451750-f4242401)

Successors of the reviewed env 22ecc07 and comms 26228c57, history kept:

| repo | commits |
|---|---|
| aify-env 098/s2-chain | 4d6acf0 (C1 scrub), 0931f88 (C3, C5, C6, C7, C8) |
| aify-comms 098/s2-chain | 219efe38 (C1, C2), 2be5d7ee (C3, C4, C9, witness E1-E3), 0c67ddd2 (witness probe fix), then this evidence |
| aify-comms 098/guided-install-plan | cc8202b1 (the C9 amendment, S2-chain.md) |

## Witness, in containers (scripts/sandbox/install-chain-witness.mjs)

| run | objects | result |
|---|---|---|
| `witness-red-on-reviewed-objects/` | the reviewed product 26228c57 plus this round's driver (throwaway branch 098/s2-witness-red, b45db2e5), env 22ecc07 | exit 1: render-after (relative key file NOT read, C6), no-key-in-argv (key PRESENT in claude `--env` argv, C1), no-early-store (old key REPLACED, C2) fail; key-from-store fails because that host never connected |
| `witness-run4-probe-errors/` | comms 2be5d7ee, env 0931f88 | exit 1 on two probe errors of mine: `httpCall` was given `/api/v1/agents` (its paths are under it already; the 404 beside the control's 401 showed the key authenticated), and the stored key was compared with the store's trailing newline |
| `witness-run5-green/` | comms 0c67ddd2, env 0931f88, wrapper 3a14911 | exit 0: all nine rows pass; cleanup read every owned resource back absent |

Each positive row runs on a host nothing ran on before (E1). The PATH row reads the skill destinations, and its
detector is shown to say PRESENT by a by-hand codex assets run (E2). Cleanup counts only Docker's not-found answer as
absent; anything else is unknown and fails the run (E3).

## Mutants (`mutants.txt`, driver `mutate.py.txt`)

18 arms, one per repaired defect, each run against its test and restored by byte copy (compared): 18 killed.

## Not covered

Native Windows end to end, codex and hermes assets on a real CLI, a real host aify-env, and a package service
through this driver (the dashboard lane's own run: aify-dashboard b5ab2d1). The witness's runtime CLIs are logging
stubs and its package sources are local.
