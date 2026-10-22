# 5. W: a second aify-env, on WSL, serves a managed agent end to end

Authorised by Q5 (DECISIONS, 2026-10-19). It is independent of H1-H4: it proves that one service drives two
environments, which multi-environment support was designed for and never shown live.

## What is on the machine (read 2026-10-22)

- WSL 2, distribution Ubuntu, running, with node v22.22.2. `.wslconfig` sets no `networkingMode`, so it is NAT, with
  `localhostForwarding=true`.
- **No `hermes` and no `claude` on the WSL PATH.**
- The WSL home already has a `~/.aify` holding `credentials`, whose origin is not established. The proof never uses
  it: it runs with its own home.
- The live service has an environment record `wsl:StevenZ-L:default`: offline, never seen. The remap names it, and
  removing it is part of this task's done-when.

## The hazards, and what each one requires

- **Starting an aify-env supersedes the one already serving at its address, and kills its holder's process tree**
  (`bin/aify-env.mjs`, the takeover branch). So the WSL aify-env:
  - runs on a non-default `--port`;
  - has `HOME` set to a fresh Linux temp directory, never under `/mnt/c`;
  - is preceded by a check, inside WSL, that nothing answers at `127.0.0.1:<port>`;
  - is never started on Windows.
- **NAT is load-bearing.** In mirrored networking, WSL's `127.0.0.1` would be Windows' own, and a takeover probe could
  reach the operator's aify-env. The run reads `wslinfo --networking-mode` (or the `.wslconfig`) first and stops if
  it is not NAT.
- **The operator's service is touched only in the second phase.** Q5 allows it, and the record is removed afterwards.

## Phase 1, in the sandbox

1. The sandbox comms from `scripts/sandbox/gate` runs in Docker, reachable from WSL at the host's address.
2. A throwaway aify-env runs in WSL, from a checkout at an exact commit, registered against the sandbox comms through
   its own `services.json`.
3. The sandbox comms lists two environments: the sandbox's own, and the WSL one.
4. A managed agent is defined on the WSL environment, started from the service, sent a message, replies, and is
   stopped. Every step is read from the sandbox service's routes and the WSL aify-env's own answer.
5. Negative control: the same agent defined on the other environment is refused by the WSL one (D9, one machine per
   defined agent).
6. Cleanup: the WSL aify-env stopped, its home removed, every created resource read back absent. Docker absence is
   asked in a way that fails closed when Docker does not answer (the S2 evidence-discriminator lesson).

## Phase 2, against the live service

The same, with the WSL aify-env registered against the operator's comms. It runs while no other test runs, and it
removes `wsl:StevenZ-L:default` and anything it created once all of 0.9 is done and deployed (Q5).

## The open question: which harness, with whose login

"End to end" means a real model turn, and WSL has no harness installed. The options:
- **A stub harness** that carries the launcher marker and answers like an agent. It proves the environment tier,
  start, deliver, console and stop, but no model turn. It needs nothing from the operator.
- **A real harness in WSL.** `claude` or `hermes` gets installed into the throwaway home and logged in. A login is
  interactive and uses the operator's account, so the login is his action (`! wsl ...`). It is removed with the
  home afterwards.

Recommended: phase 1 with the stub, now; phase 2 with a real harness, if he does the one login.
