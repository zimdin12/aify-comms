# B1: who owns the residents (contract draft, 2026-10-14)

## The problem

Residents run in the persistent `herdr-aify`. Its server environment carries no `AIFY_ENV_INSTANCE`, so
`aify-lifetime.sh` stamps every resident record `default`. The only live aify-env is a dedicated
`herdr-aify env` invocation, whose state lives under its fresh invocation scope. Nothing serves
`default`, so aify-env sees no resident: 16 records, the `default` pid dead, verified 2026-10-14.

Agreed with the reviewer: separate the logical owner of residents from the daemon and Herdr
incarnations. A dedicated invocation keeps a fresh scope, refuses takeover, and does no recovery for
its managed lifetimes.

## The contract

1. **The namespace is the aify home.**
   - The resident-owner role is per `~/.aify` directory (`AIFY_HOME`). Windows and WSL have different
     homes, so they are different environments and never share a role.
   - `default` names the role inside one home. It is not a global name.
   - Resident records keep `instance: "default"`, and no record is relabelled.

2. **The role is a held OS lock, not a lease and not a file.**
   - The holder is the one process listening on the role endpoint:
     - Windows: the named pipe `\\.\pipe\aify-resident-owner-<sha256(home)[:16]>`.
     - Linux: the abstract socket `\0aify-resident-owner-<sha256(home)[:16]>`.
   - Binding is atomic and exclusive. The kernel releases it when the process exits.
   - There is no expiry. A paused holder still holds the role, so there is no stale holder to fence.
   - A second bind fails, and so does a bind that fails for any other reason. Either way the daemon
     runs without the role (fail closed). It reports why in `/health`, and `aify-env doctor` shows it.
   - Nothing is ever acquired by reading something.
   - macOS has no abstract sockets. It is out of scope until a socket in the home, guarded by
     connect-then-unlink, is specified.
   - Measured 2026-10-14 with Node 22, on Windows and in WSL Ubuntu:
     - a second bind while the first holds fails `EADDRINUSE`;
     - after the first closes, a bind succeeds;
     - after the holding process is killed (SIGKILL), a bind succeeds.

     The probes are `pipeprobe2.mjs` and `pipeprobe3.mjs`; they are quoted in the commit that builds
     this.
   - An elevated holder may make a normal process's bind fail `EACCES`. That is "not acquired", as
     above, and is reported. It never causes a retry under another name.

3. **Who acquires it.** Any aify-env, ordinary or dedicated:
   - tries at boot, and again every sweep while it does not hold the role;
   - the first bind wins.

   One daemon can hold the resident role and its own managed-invocation scope at the same time. The
   operator never has to run a second daemon.

4. **The epoch is the incarnation.**
   - Once bound, the holder writes `~/.aify/env/default.json` (the existing descriptor format) with its
     url, pid, startedAt and a boot UUID.
   - Hooks keep reading the `default` descriptor, unchanged.
   - The descriptor is a pointer for routing, never proof of ownership.

5. **Enforcement is at each effect, by asking the lock, never by consulting a cached set.**
   - **Resident turn events:** a daemon applies an event addressed to `default` only while it is
     listening on the role endpoint. Otherwise it refuses with `not-owner`, so a stale descriptor that
     reaches the wrong daemon changes nothing.
   - **Resident lifecycle:** stop and kill for residents require the role.
   - **Publication:**
     - Residents appear only in the holder's complete snapshot.
     - A daemon without the role publishes managed lifetimes only. Each of these snapshots is complete
       for the population it declares (C5).

6. **Releasing the role is ordered.** On shutdown:
   1. stop applying resident events;
   2. publish a snapshot without residents and wait for it to be sent, bounded;
   3. close the endpoint.

   So no publication from the old holder includes residents after a successor could have acquired the
   role. A crash skips these steps. The successor's first complete snapshot then supersedes the dead
   publisher, as C5 does for any dead publisher.

7. **Continuity without inventing idle (P-1).**
   - Turn state for the role lives in `~/.aify/env/default.turns.json`. Only the holder writes it,
     because the lock serialises writers.
   - A new holder loads that file. An open turn stays open; the operator's ruling 1791194105933 holds:
     no wall-clock cap.
   - A resident with no turn knowledge has none. It is never reported idle because knowledge was lost.

8. **Managed lifetimes are unchanged.** `ManagedLifetimes` still stamps the invocation scope on its
   children. Recovery stays none, and no managed lifetime is ever relabelled `default`.

## Controls, each paired with its success case

| Contender or situation | Expected |
|---|---|
| ordinary and dedicated start together | exactly one holds the role; the other runs managed-only |
| two dedicated invocations | the same |
| a bind failing for a reason other than in-use | no role, reported; residents are never applied by the non-holder |
| a stale `default.json` pointing at a non-holder | the event is refused `not-owner` and state is unchanged |
| the holder pauses (process frozen), then resumes | it still holds the role; no successor acquired meanwhile |
| the holder stops cleanly | its last snapshot has no residents; the successor acquires on its next sweep and loads the turns file |
| the holder crashes with a turn open | the successor loads the open turn: busy, not idle |
| a resident relaunched across a transfer | continuity: the new holder applies its events |
| managed lifetimes of a previous invocation | not adopted; no resurrection |

## Out of scope

- Moving residents between homes.
- Several comms services.
- macOS.
