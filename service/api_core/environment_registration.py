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

from service.api_core.serialization import version_text as _version_text


async def _record_environment_registration(
    db, existing, env_id, req, effective_roots, runtimes, requested_status, next_metadata,
    registered_at, now,
) -> None:
        """Update the environment row if it exists, create it if it does not.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        #: WHERE THE VERSION ACTUALLY ARRIVES, which is not where this column was reading it.
        #:
        #: MEASURED 2026-09-06 on the operator's own host: `metadata.bridgeVersion` read `0.6.2` and
        #: the `bridge_version` COLUMN read `0.6.0`, on one row, written by one live claimer. So
        #: `tier-version` reported the host tier two versions behind while aify-env was current --
        #: the exact false red that makes a check get switched off.
        #:
        #: aify-env sends `bridgeId` TOP-LEVEL and the rest of its identity inside `metadata`, and
        #: `api.mjs` says why in its own comment: sending `bridgeStartedAt` at the top level looked
        #: right and was silently ignored, because the arbitration reads it from `metadata`.
        #: `bridgeVersion` rides in that same identity object -- and this column was fed from
        #: `req.bridgeVersion`, which NOTHING has sent since v0.6.2 deleted the environment-bridge
        #: cluster. `_kept()` then did its job perfectly: it preserved the last value a legacy bridge
        #: wrote, and froze it there for good.
        #:
        #: BOTH ENDS OF THE FIELD, which is this repo's own rule: a value with no reader and a reader
        #: with no writer are one defect from opposite sides. The identity's home is `metadata`; the
        #: column is its projection, so it reads from where the value lives.
        #:
        #: ONLY A CALLER THAT DECLARES A BRIDGE MAY SET THIS, WHICHEVER CARRIER IT USES.
        #:
        #: An independent review, 2026-09-07, found the first version of this claiming a safety
        #: property it did not have. The reasoning was that `environment_heartbeat` drops the whole
        #: `bridge*` METADATA namespace from a caller that sends no `bridgeId` -- true, and it covers
        #: one of the two carriers. The TOP-LEVEL `req.bridgeVersion` is stripped by nothing and was
        #: read FIRST, so the forgery the negative control was written to close stood open through
        #: the other door. Reproduced against a row held by a live host tier:
        #:
        #:     POST /environments/heartbeat  {"id": ENV, "bridgeVersion": "9.9.9"}   # no bridgeId
        #:     -> column 9.9.9, metadata.bridgeVersion still 0.6.2, bridge_id still the real claimer
        #:
        #: `tier-version` reads the column first, so that reports GREEN on a host whose tier is
        #: behind -- the exact false green that check was rewritten once to remove, reachable by an
        #: unauthenticated call on a keyless deployment.
        #:
        #: PRE-EXISTING, NOT INTRODUCED: before this change `req.bridgeVersion` was the only source,
        #: so the hole is older than the fix and the fix narrowed it (the incumbent's next beat now
        #: repairs a forged value instead of it freezing forever). What was new was the CLAIM. Both
        #: carriers are gated now, so the claim is true rather than nearly true.
        #:
        #: `bridgeId` IS A SELF-ASSERTED STRING, not an authenticated identity -- possession of any
        #: non-blank one passes. It is the same gate the metadata namespace already uses and no
        #: weaker; saying it "proves" anything was the second overstatement here.
        declares_a_bridge = bool(str(req.bridgeId or "").strip())
        incoming_bridge_version = (
            (
                str(req.bridgeVersion or "").strip()
                #: STRINGIFIED DEFENSIVELY. `metadata` is `dict[str, Any]`, so unlike the pydantic
                #: `Optional[str]` above it can carry a dict or a list -- which `str()` would write
                #: into the column as a Python repr (`{'evil': 1}`). It fails safe downstream, but a
                #: column holding a repr is a column nothing can parse, and it persists via `_kept`
                #: until a well-formed beat replaces it.
                or _version_text((next_metadata or {}).get("bridgeVersion"))
            )
            if declares_a_bridge else ""
        )
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
            preserved_bridge_version = _kept(incoming_bridge_version, "bridge_version")
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
                    incoming_bridge_version,
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
