"""Which machine a host request comes from, beyond the API key (external review of 0.8.1, HIGH 2).

THE DEFECT. Every agent holds the API key, and the routes a host uses to speak for its machine trusted
the key alone. A key holder could push a machine's agent definitions with the bridge id `GET
/environments` hands out (rewriting another agent's instructions, model and environment, or withdrawing
every agent the machine defines), or register as the host tier with a later start time and take the
environment row first.

THE RULE. aify-env sends `X-Aify-Host-Proof`, derived from a secret kept in that host's `~/.aify`
(aify-env `lib/host-secret.mjs`). The first proof a machine presents is recorded here, as a digest; from
then on that machine's heartbeat, definition push, and change-request claim and report need the same
proof. A machine that has never presented one is let through as before, so a host still running an older
aify-env keeps working until it upgrades. An operator resets a machine's proof (`POST
/host-proofs/{machineId}/reset`) when the host's secret is lost or replaced.

WHAT IT DOES NOT DO. A process that can read the host's secret file can make its proof, as it can read
every key on that host. And the first proof wins: a key holder who presents one for a machine before its
aify-env ever has locks that host out until an operator resets it, which is loud, not silent.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Iterable

from service.api_core.serialization import _normalize_machine_id

#: The header aify-env sends; its `lib/plugins/aify-comms/api.mjs` names the same one.
HOST_PROOF_HEADER = "X-Aify-Host-Proof"


def presented_proof(request) -> str:
    try:
        return str(request.headers.get(HOST_PROOF_HEADER) or "").strip()
    except Exception:
        return ""


def _digest(proof: str) -> str:
    """Stored instead of the proof, so a copy of the database cannot be replayed as one."""
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


async def host_proof_refusal(db, machine_ids: Iterable[str], presented: str, now: str) -> str:
    """Why this request may not speak for these machines, or "". A machine's first proof is recorded here,
    inside the caller's transaction, so it is kept only if the caller commits."""
    for machine_id in dict.fromkeys(_normalize_machine_id(m) for m in machine_ids):
        if not machine_id:
            continue
        row = await (await db.execute(
            "SELECT proof_digest FROM host_proofs WHERE machine_id = ?", (machine_id,))).fetchone()
        if row is None:
            if presented:
                await db.execute(
                    "INSERT OR IGNORE INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, ?)",
                    (machine_id, _digest(presented), now))
            continue
        if not presented or not hmac.compare_digest(row["proof_digest"], _digest(presented)):
            said = "presents none" if not presented else "presents a different one"
            return (f"machine {machine_id} proves itself with a host proof and this request {said}. If this is "
                    f"that machine's aify-env after its ~/.aify/host-secret was replaced, an operator resets it: "
                    f"POST /host-proofs/{machine_id}/reset")
    return ""
