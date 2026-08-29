"""Recording an environment's registration: the same row, updated or created.

Extracted from `environment_heartbeat` in `service/routers/environments.py` in v0.5.4;
`test_environment_heartbeat_split_is_inert.py` inlines it back and AST-compares against the
pre-split fixture. The body is at its original 8-space column so the SQL literals are preserved
byte-for-byte.

TWO STATEMENTS, ONE ROW SHAPE. The UPDATE writes eleven columns; the INSERT writes those eleven plus
`id` and `registered_at`. That difference is the whole design: an environment re-registering must
not have its identity or its first-seen time rewritten, and everything else about it is whatever the
bridge just reported.

THE FAILURE MODE IS SILENT AND ASYMMETRIC. Add a column to the INSERT and forget the UPDATE and a
FRESH environment gets it while every re-registering one keeps a stale value forever -- and
re-registration is the common case, since a bridge heartbeats through here on every restart. Add it
to the UPDATE and forget the INSERT and a brand-new environment is missing it until its second
heartbeat. `test_environment_upsert_columns_agree.py` pins the relationship rather than either
statement.
"""
from __future__ import annotations

import json


async def _record_environment_registration(
    db, existing, env_id, req, effective_roots, runtimes, requested_status, next_metadata,
    registered_at, now,
) -> None:
        """Update the environment row if it exists, create it if it does not.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        if existing:
            # A HEARTBEAT THAT DECLARES NO BRIDGE KEEPS THE ONE ON THE ROW.
            #
            # `bridgeId` is optional on `EnvironmentHeartbeat`, and the model already says why: "a
            # bridge started by hand has no launcher and sends neither; that is normal rather than
            # missing data". The environment TIER advertising its own capabilities is the same
            # shape -- it describes the host, it does not claim to own the bridge.
            #
            # Writing `req.bridgeId or ""` blanked the column for those callers, and the blanking is
            # not the expensive half. Supersession is gated on BOTH sides carrying an id:
            #
            #     if existing and existing["bridge_id"].strip() and req.bridgeId.strip():
            #
            # so ONE id-less heartbeat disarms the arbitration between a stale bridge and a fresh
            # one, permanently and silently, and the `bridgeStartedAt` comparison behind it never
            # runs again. A guard whose input has been erased reads exactly like a guard with
            # nothing to arbitrate.
            #
            # The INSERT below keeps `or ""`: a row being created has no prior bridge to preserve.
            #: ONE RULE, SEVEN FIELDS. Each is `Optional[...] = None` on the request model, so a
            #: caller that omits one has said nothing about it -- and `req.X or ""` turned that
            #: silence into an erasure. The model already states the distinction for roots: "null
            #: means the service said nothing about roots -- keep what we had. An empty ARRAY means
            #: it said there are none."
            def _kept(incoming, column):
                return str(incoming or "").strip() or str(existing[column] or "")

            # LABEL BELONGS IN THE SET AFTER ALL, and the reason it was left out was wrong. The
            # argument was that `req.label or env_id` falls back to a real default rather than a
            # blank -- true, and irrelevant on an UPDATE: the "real default" is the raw environment
            # id, so one advertisement that says nothing about the label replaces the operator's
            # "Windows on StevenZ-L" with "windows:StevenZ-L:default". Measured, not reasoned about.
            #
            # The INSERT below keeps `req.label or env_id`: a row being created has no prior label,
            # and its id is the honest name until somebody gives it one.
            preserved_label = _kept(req.label, "label")

            preserved_bridge_id = _kept(req.bridgeId, "bridge_id")
            preserved_bridge_version = _kept(req.bridgeVersion, "bridge_version")
            preserved_machine_id = _kept(req.machineId, "machine_id")
            preserved_os = _kept(req.os, "os")
            preserved_kind = _kept(req.kind, "kind")
            preserved_launcher_version = _kept(req.launcherVersion, "launcher_version")
            preserved_launcher_fingerprint = _kept(
                req.launcherRegistryFingerprint, "launcher_registry_fingerprint"
            )
            await db.execute(
                """
                UPDATE environments
                SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?,
                    bridge_version = ?, launcher_version = ?,
                    launcher_registry_fingerprint = ?, cwd_roots = ?, runtimes = ?,
                    status = ?, metadata = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    preserved_label or env_id,
                    preserved_machine_id,
                    preserved_os,
                    preserved_kind,
                    preserved_bridge_id,
                    preserved_bridge_version,
                    preserved_launcher_version,
                    preserved_launcher_fingerprint,
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    now,
                    env_id,
                ),
            )
        else:
            await db.execute(
                """
                INSERT INTO environments (
                    id, label, machine_id, os, kind, bridge_id, bridge_version,
                    launcher_version, launcher_registry_fingerprint,
                    cwd_roots, runtimes, status, metadata, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    env_id,
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    req.launcherVersion or "",
                    req.launcherRegistryFingerprint or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    registered_at,
                    now,
                ),
            )
