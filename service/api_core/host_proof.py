"""Which machine a host request comes from, beyond the API key (external review of 0.8.1, HIGH 2).

THE DEFECT. Every agent holds the API key, and the routes a host uses to speak for its machine trusted
the key alone. A key holder could push a machine's agent definitions with the bridge id `GET
/environments` hands out (rewriting another agent's instructions, model and environment, or withdrawing
every agent the machine defines), or register as the host tier with a later start time and take the
environment row first.

THE RULE. aify-env sends `X-Aify-Host-Proof`, derived from a secret kept in that host's `~/.aify`
(aify-env's `host-secret.mjs`). The proof of the first heartbeat that succeeds for a machine is recorded
here, as a digest; from then on that machine's heartbeat, definition push, change-request claim and report, spawn
and terminal claims, spawn updates and terminal-control reports need the same proof. A machine that has never presented one is let through as before, so a host still running an older
aify-env keeps working until it upgrades. An operator resets a machine's proof (`POST
/host-proofs/{machineId}/reset`) when the host's secret is lost or replaced.

WHAT IT DOES NOT DO. A process that can read the host's secret file can make its proof, as it can read
every key on that host. And the first proof wins: a key holder who presents one for a machine before its
aify-env ever has locks that host out until an operator resets it, which is loud, not silent.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Iterable

from service.api_core.serialization import _normalize_machine_id

#: The header aify-env sends; its comms plugin's `api.mjs` names the same one.
HOST_PROOF_HEADER = "X-Aify-Host-Proof"


def presented_proof(request) -> str:
    try:
        return str(request.headers.get(HOST_PROOF_HEADER) or "").strip()
    except Exception:
        return ""


def _digest(proof: str) -> str:
    """Stored instead of the proof, so a copy of the database cannot be replayed as one."""
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HostProofVerdict:
    """What one request's proof says: why it is refused ("" when not), and the machines it names that have no
    proof yet, which a heartbeat that succeeds enrolls."""
    refusal: str
    unenrolled: tuple[str, ...] = ()


async def judge_host_proof(db, machine_ids: Iterable[str], presented: str) -> HostProofVerdict:
    """READS ONLY. A machine is enrolled only by a heartbeat that succeeded (`enroll_host_proof`): recording it
    here enrolled a machine from a definition push that was then refused, locking its real host out (review of
    08f3e7e5, H2-R2). The caller holds the write lock (`BEGIN IMMEDIATE`) from this read to its write, so a
    proof recorded meanwhile cannot slip between them (H2-R1)."""
    unenrolled = []
    for machine_id in dict.fromkeys(_normalize_machine_id(m) for m in machine_ids):
        if not machine_id:
            continue
        row = await (await db.execute(
            "SELECT proof_digest FROM host_proofs WHERE machine_id = ?", (machine_id,))).fetchone()
        if row is None:
            unenrolled.append(machine_id)
            continue
        if not presented or not hmac.compare_digest(row["proof_digest"], _digest(presented)):
            said = "presents none" if not presented else "presents a different one"
            return HostProofVerdict(
                f"machine {machine_id} proves itself with a host proof and this request {said}. If this is "
                f"that machine's aify-env after its ~/.aify/host-secret was replaced, an operator resets it: "
                f"POST /host-proofs/{machine_id}/reset")
    return HostProofVerdict("", tuple(unenrolled))


async def enroll_host_proof(db, verdict: HostProofVerdict, presented: str, now: str) -> None:
    """Record `presented` for the machines `verdict` found without a proof. Called by a heartbeat that
    succeeded, inside the transaction whose lock it judged under; nothing to do once a machine is enrolled."""
    if not presented:
        return
    for machine_id in verdict.unenrolled:
        await db.execute("INSERT INTO host_proofs (machine_id, proof_digest, recorded_at) VALUES (?, ?, ?)",
                         (machine_id, _digest(presented), now))
