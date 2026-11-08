# Scope decisions after the 2026-11-08 re-check

The comms lead decided these, as manager of the stack roadmap. The lane tables they rest on are linked
from the State page. Builds resume one issue per change under
[Scope first](WORKING-AN-ISSUE.md#scope-first).

## Published changes

No published change is reverted. Each fix that went beyond its goal works and is tested, and removing
it now would be a new change with its own risk. Each is recorded here so it does not set a precedent:

| Change | Beyond its issue's goal | Decision |
|---|---|---|
| env#21 (4e10bc5) | boot cancellation custody, Runner-wide fence, post-start plugin check, later advertising | keep; E4 observes the advertising delay |
| env in-flight spawn custody (5a08abb) | no issue; came from lead review | keep as a process-custody guarantee |
| env#17 (376fa36) | takeover policy: incumbent ended on Linux, birth-unknown refusal (69), raw starts refused during the fence | keep; E4 must show a takeover from dde9d6d before the joint switch |
| env#16, env#32 | extra trusted-authority grants; installer reconnect and keyless readiness | keep |
| wrapper#14 (d07ba67) | readiness waiter for an app-server that dies during start | keep |
| wrapper#16 (1c9645d) | category format and readers bundled with doc corrections | keep |
| dashboard#21 | stdio uploader; live agents use HTTP only | keep, no further work |
| comms staging core (55041406) | general preflight and dispatch before any runnable scenario | keep the safety parts; no more framework |

## Open work, in order

The goal is the joint switch: what is built goes live together, and then the install on a second PC.

1. comms: make E4 one runnable command and run it (baseline 1c9611b3/dde9d6d/dd04bd4 to the current
   mains). Fix the reply verifier's 405 first. No new shared code until a second scenario needs it.
2. wrapper#20, then wrapper#26: the narrow changes only, tens of product lines each. The dropped criteria
   (anonymous retention scheme, Windows ACL test, hard-kill sweep, tri-state collection) are not part of
   these issues; reopen one only if a real case shows the need.
3. env#19 follow-up: check whether an invalid PTY PID can reach the branch that leaves its handle open;
   if it can, close it (about 15 lines, one test).
4. dashboard#18 defaults: wrapper's flag default, then env's shipper (no flag injection, ship every
   lease-home record).
5. E2, then E3 and E1, as plain scenarios on the same layout.
6. The joint switch: comms pin bump with its queued test changes, rebuild, install; the operator restarts
   aify-env.

Everything else in the lane tables waits until after the switch. Each one is built against its trimmed
criteria, with a size budget.
