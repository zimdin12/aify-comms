// Which bridge PROCESS this is — the identity the control plane uses to tell one from another.
//
// One uuid, minted once at startup, read from 46 places. v0.5.4 layer 0 of the server.js decomposition.
//
// IT IS NOT COSMETIC AND IT IS NOT THE AGENT'S ID. It is the key for the `bridge_instances` table, it is
// what heartbeats and claims are attributed to, and it is how the service decides which bridge currently
// OWNS an environment — so when a second bridge starts for the same environment, this is the value that
// distinguishes the newcomer from the one being superseded. A bridge whose instance id collided with
// another's would have its claims credited to the wrong process, and the reaping that follows a supersede
// would target the wrong worker set. That has happened once in this project's history for a different
// reason, and it took a managed fleet down.
//
// SO THE TWO PROPERTIES THAT MATTER ARE UNIQUENESS ACROSS PROCESSES AND STABILITY WITHIN ONE. Every reader
// assumes both: uniqueness so two bridges are distinguishable, stability so the id a bridge registered under
// is the same one its heartbeats and claims carry minutes later. `randomUUID()` gives the first and
// module-scope evaluation gives the second — a per-call generator would satisfy neither, and would fail
// silently, because each individual value would still look like a valid id.
//
// DISTINCT FROM `launch-identity.mjs`, which owns what this process was TOLD it is — the agent id and role
// its wrapper handed it in the environment. This is what the process observably IS, minted rather than
// received, and it exists even for a bridge with no agent identity at all.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { randomUUID } from "crypto";

export const BRIDGE_INSTANCE_ID = randomUUID();
