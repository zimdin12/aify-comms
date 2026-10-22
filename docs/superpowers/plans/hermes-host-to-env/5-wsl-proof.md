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

## The hazards, and what each one requires (revision 2)

**What aify-env does when its port is held.** At ad07615, `bin/aify-env.mjs` treats the holder by what it is:

| holder | behaviour |
|---|---|
| does not identify as an aify-env | left alone; aify-env exits 69 |
| an aify-env that is running processes | refused, unless `--force` is given |
| an idle aify-env | superseded: its process tree is killed |

A foreign idle aify-env that appears between any preflight and the `listen` would be killed. So the proof makes that
branch unreachable at the launch itself:
- **The WSL aify-env listens on `--port 0`.** The OS picks a free port, a `listen` on port 0 cannot fail for being in
  use, and the takeover branch is never entered. The bound port is read from the daemon's own answer. Port 0 is
  legal (`lib/port-argument.mjs`); it is what aify-env's own suite uses.
- **`--force` is prohibited** in every command of the proof.
- **Its `HOME` is a fresh Linux temp directory,** never under `/mnt/c`, and it is never started on Windows.
- **Every root it reads or writes is sealed to that directory, not only `HOME`** (revision 3):
  - `USERPROFILE`, which `lib/host-config.mjs` prefers over `HOME`;
  - `AIFY_ENV_PROCESS_RECORD`, because ordinary startup rewrites the owned-process record even after a private
    port-0 bind. Before `a-port-zero-start-is-not-a-takeover.test.js`, an unsealed record let a port-0 daemon reap a
    live fleet (2026-08-26);
  - `AIFY_SERVICE_REGISTRY`, set to a real file inside the temp home and never to an empty string. An empty value
    selects the real registry (`tests/_sealed-daemon-env.mjs`).

  The launch uses aify-env's existing isolation path, the sealed daemon environment its own suite starts daemons
  with, and no new framework. Sentinels are placed outside the temp root, at the paths each variable would resolve to
  unsealed, and are read back byte-identical after the run.

**Controls before any authorised WSL run.** These are aify-env tests in a sealed home, via `npm test`. Each fake holder
sits at a fixed port, and the launch under test uses `--port 0`.

| case | what is checked |
|---|---|
| a foreign idle aify-env | untouched |
| a foreign busy aify-env | untouched |
| an unknown listener | untouched |
| owned, empty endpoint (healthy) | the launch binds and answers |

Each adverse case asserts two things (revision 3):
- **The foreign holder survives:** its process is still running and its port still answers.
- **The new launch bound and answered on its own endpoint.** It is identified by its own answer, carrying the pid
  and instance the test started, at a port different from the fake's.

Survival alone stays green when a wrong fixed port lands the launch in the busy or unknown refusal branches, because
those leave the holder alone too.

**The wrong-port mutant:** `--port 0` replaced by the fake's port. Its first failing assertion is kept with the
evidence:
- in the idle case, the survival check fails, because the holder is superseded;
- in the busy and unknown cases, the new-endpoint check fails, because the launch exited 69 without binding.

The sentinel read-back above runs in all four cases.

**NAT is load-bearing, read at runtime.** In mirrored networking, WSL's `127.0.0.1` is Windows' own. The run reads
the effective mode inside WSL with `wslinfo --networking-mode` at launch time. It answered `nat` on 2026-10-22, which
is an observation of that day only. `.wslconfig` is a saved setting, not the running mode. If the command is absent
or answers anything other than `nat`, the mode is UNKNOWN and the run stops.

**The operator's service is touched only in the second phase.** Q5 allows it.

## Phase 1, in the sandbox

1. The sandbox comms from `scripts/sandbox/gate` runs in Docker, reachable from WSL at the host's address.
2. A throwaway aify-env runs in WSL, from a checkout at an exact commit, registered against the sandbox comms through
   its own `services.json`.
3. The sandbox comms lists two environments: the sandbox's own, and the WSL one.
4. A managed agent is defined on the WSL environment, started from the service, sent a message, replies, and is
   stopped. Every step is read from the sandbox service's routes and the WSL aify-env's own answer.
5. Negative control: the same agent defined on the other environment is refused by the WSL one (D9, one machine per
   defined agent).
6. Cleanup is bound to exact created resources. Each is recorded when it is made, from the creating command's own
   answer, and only those are removed:
   - the temp `HOME` path;
   - the WSL aify-env's pid and the bound port;
   - the sandbox containers and network, by id;
   - the environment id the WSL aify-env registered, read from its advertisement;
   - the agent ids defined.

   Each is read back absent. Docker absence is asked in a way that fails closed when Docker does not answer (the S2
   evidence-discriminator lesson). The WSL home's existing `~/.aify` is of unknown origin and is never touched.

## Phase 2, against the live service

The same, with the WSL aify-env registered against the operator's comms. It runs while no other test runs.
Afterwards, it removes what it created, by the exact ids recorded above.

**The pre-existing offline record is a separate removal.** `wsl:StevenZ-L:default` is not something this proof
created, so its removal has its own authority: Q5 and the remap, once all of 0.9 is done and deployed.
- Its identity is bound before removal by reading the service's record for that exact id: its machine, its created
  and last-seen fields, and its agents.
- A display label or a missing heartbeat is not identity.
- If the record cannot be bound, or it lists an agent, it is left in place and reported, not removed.

## The open question: which harness, with whose login

"End to end" means a real model turn, and WSL has no harness installed. The options:
- **A stub harness** that carries the launcher marker and answers like an agent. It proves the environment tier,
  start, deliver, console and stop, but no model turn. It needs nothing from the operator.
- **A real harness in WSL.** `claude` or `hermes` gets installed into the throwaway home and logged in. A login is
  interactive and uses the operator's account, so the login is his action (`! wsl ...`). It is removed with the
  home afterwards.

The default is phase 1 with the stub. It qualifies the environment side only, and nothing it proves is called a
model turn. The real model turn stays pending, recorded as not done, and the operator is not asked for the login now.
