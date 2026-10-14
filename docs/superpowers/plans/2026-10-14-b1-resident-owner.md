# B1: who owns the residents (contract, revision 3, 2026-10-14)

## The problem

Residents run in the persistent `herdr-aify`. Its server environment carries no `AIFY_ENV_INSTANCE`, so
`aify-lifetime.sh` stamps every resident record `default`. The only live aify-env is a dedicated
`herdr-aify env` invocation, whose state lives under its fresh invocation scope. Nothing serves
`default`, so aify-env sees no resident: 16 records, the `default` pid dead, verified 2026-10-14.

Agreed with the reviewer: separate the logical owner of residents from the daemon and Herdr
incarnations. A dedicated invocation keeps a fresh scope, refuses takeover, and does no recovery for
its managed lifetimes. Revision 1 (41cb466f) was revised for two holes:
- **the C5 handover:** comms orders publications only within one `(machineId, instance)` key;
- **the physical home identity.**

## The contract

1. **The home is one physical directory, resolved one way by everyone.**
   - The aify home is `<os home>/.aify`. This slice has no override: `AIFY_HOME` is not read by the
     launcher or by aify-env today, and it is not introduced for the lock alone.
   - Its identity is `realpath` of that directory, using `fs.realpathSync.native` on Windows, which
     resolves junctions and 8.3 names. Windows folds it to lower case.
   - Every party that names the role derives the role from this identity:
     - the lock;
     - the descriptor, the turns journal and the generation file;
     - the launcher's resident records: `aify-lifetime.sh` resolves `$HOME/.aify`, and on Windows
       `cygpath -m` makes it the same path aify-env's `homedir()` yields.
   - A test pins that the launcher and aify-env resolve the same identity.
   - **A home shared across operating systems is refused for the role.** These are WSL on a drvfs or 9p
     mount (the home under `/mnt/<drive>` or `/mnt/wsl`), and Windows on a `\\wsl$` / `\\wsl.localhost`
     path. The OS lock cannot exclude across that boundary, so such a daemon runs without the role and
     reports why.
   - Two spellings of one directory on one OS resolve to one identity, so one owner.

2. **The role has its own name: `residents`.**
   - An ordinary aify-env's instance is already `default`, with its own descriptor (`default.json`) and
     its own C5 publisher key. The role therefore never borrows that name.
   - Its files: `~/.aify/env/residents.json` (descriptor), `residents.turns.json`,
     `residents.generation`.
   - New resident records are stamped `instance: "residents"`. The launcher exports
     `AIFY_ENV_INSTANCE=residents` for a resident, so its hooks read `residents.json`.
   - Records stamped `default` by launchers from before this change are not relabelled. They stay
     unbound until relaunched (policy class "unbound", which needs the operator's ruling).

3. **The role is a held OS lock, not a lease.**
   - The holder is the one process listening on the role endpoint:
     - Windows: the named pipe `\\.\pipe\aify-residents-<sha256(identity)[:16]>`.
     - Linux: the abstract socket `\0aify-residents-<sha256(identity)[:16]>`.
   - The bind is atomic and exclusive, and the kernel releases it on exit. There is no expiry; a paused
     holder still holds.
   - Any failed bind means no role, reported, failing closed. That includes `EACCES` against an elevated
     holder. Nothing is acquired by reading.
   - macOS is outside this slice's qualification. No alternative for it is approved by leaving it out.
   - Measured 2026-10-14, Node 22, on Windows and in WSL Ubuntu:
     - a second bind fails `EADDRINUSE`;
     - after close, the bind succeeds;
     - after the holder is SIGKILLed, the bind succeeds.

     These are author-run probes. The reviewer has not independently qualified them.

4. **Who holds it.** Any aify-env, ordinary or dedicated:
   - tries at boot, and on every sweep while it does not hold the role;
   - the first bind wins.

   One daemon holds the role and its own managed scope.

5. **Generation, under the lock.**
   - Right after binding, the holder reads `residents.generation`, adds one, and writes it durably
     before its first publication.
   - An unreadable or invalid file means the daemon releases the endpoint and runs without the role.
     It never guesses a generation.
   - The lock serialises writers, so successive holders' generations strictly increase.

6. **Publication (C5) keys and populations.**

   | publisher key | population | complete for |
   |---|---|---|
   | `(machineId, "residents-<sha256(identity)[:12]>")` | every resident lifetime in this home | the role holder only |
   | `(machineId, <own instance>)` | that daemon's managed lifetimes | each daemon, holder or not; an ordinary daemon's own instance is `default`, a dedicated one's `herdr-<invocation>` |

   - **The residents key carries the home** (revision 3). A machine id comes from the platform and host
     or `AIFY_MACHINE_ID` (`advertise.mjs:174-179`), not from the home. So two homes on one machine, two
     users or two profiles, would otherwise share `(machineId, "residents")`. Each could legally hold its
     own lock, and the receiver would read their unrelated snapshots as one publisher:
     - equal generations are a `conflict`;
     - a higher generation deletes the other home's rows.

     The home-scoped suffix gives each home its own key. Within one home the key is stable across
     holders, so succession ordering (below) is unchanged. Machine id and the receiver's association and
     conflict rules are not changed.
   - Residents appear under their home's residents key and nowhere else.
   - On transfer the successor publishes under the same key with a higher generation. The receiver
     (`agent_state_shadow.py:150-161,190-245`) orders same-key publications by generation and replaces
     the whole key's rows on a snapshot. So:
     - the successor's first snapshot is the sole resident association;
     - any later-arriving publication of the dead holder's is refused `stale`.

     No change to the receiver's ambiguity handling is made or needed.
   - Non-holders' managed publications are on their own keys and unaffected.

7. **Enforcement at each effect, by asking the lock.**
   - **Resident turn events:** a resident turn event addressed to `residents` is applied only while this
     process holds the endpoint. Otherwise it is refused `not-owner`. A stale descriptor that reaches a
     non-holder therefore changes nothing.
   - **Lifecycle:** resident lifecycle requires the role.
   - **Publication:** the residents population is composed only while the role is held.

8. **Releasing the role.** On shutdown, in order:
   1. stop admitting resident events (`not-owner` from here on);
   2. wait for resident operations already entered to finish, bounded;
   3. close the endpoint.

   No withdrawal publication is needed, because the successor's higher generation supersedes on the same
   key. A crash skips these steps with the same result. The holder's sender awaits nothing for
   ordering; ordering is the generation's.

9. **Continuity without inventing idle (P-1).**
   - Only the holder writes `residents.turns.json`. A new holder loads it, so an open turn stays open,
     busy, with no wall-clock cap (ruling 1791194105933).
   - A resident with no turn knowledge is never reported idle because knowledge was lost.

10. **Managed is unchanged.** It keeps the invocation scope and its own publisher key. Recovery stays
    none, and no managed lifetime is ever relabelled or moved to `residents`.

## Controls

The receiver-side cases are checked against the mirror and the current decision, not only bind results.

| Case | Expected |
|---|---|
| A publishes resident R, then crashes; B acquires and publishes R; a delayed A publication arrives | B's rows alone; A's late post refused `stale`; the decision reads B's state |
| clean handover: A stops (ordered release), B acquires | the same, with no gap in which events are applied by both |
| A's release wait times out | the endpoint is still closed; B's generation still supersedes |
| a non-holder publishes managed lifetimes while a holder publishes residents | both keys current; no ambiguity |
| ordinary + dedicated contenders; two dedicated contenders | exactly one holder |
| a stale `residents.json` pointing at a non-holder | the event is refused `not-owner` |
| a paused holder that resumes | it still holds; no successor meanwhile |
| a crash with a turn open | the successor loads it: busy |
| an open lifetime persisted with last event t2, restored by the next holder, then an admitted same-lifetime turn-end at t1 < t2 | refused as out of order; the original anchor and open state are unchanged and it stays busy; a later valid end closes it (watermark continuity, not only a restored busy bit) |
| one home spelled two ways on one OS | one identity, one owner |
| two different homes | two owners, both succeed |
| two homes on one machine (same machineId, same service), distinct residents, both holders publishing (equal and unequal generations) | two publisher keys; the receiver keeps both homes' rows; no `conflict`, no deletion across homes; the decision reads each resident from its own home's key |
| a home on a cross-OS mount | no role, reported |
| an unreadable generation file | no role, reported |
| managed lifetimes of a previous invocation | not adopted |

## Out of scope

- Moving residents between homes.
- Several comms services.
- macOS.
- `AIFY_HOME` overrides.
