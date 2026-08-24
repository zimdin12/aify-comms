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
