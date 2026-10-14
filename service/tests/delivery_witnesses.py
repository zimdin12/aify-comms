"""The P7 witnesses' shared fixture: what a turn IS, frozen apart from how it reaches the service.

P7 moves agent status to aify-env (plan 2026-10-02-aify-env-owns-agent-state.md, P0 C6/C8). The delivery
decisions it rebuilds -- the send-time queue (ledger B9), the claim hold (B17), its steer bypass (B19), the
reminder skip (B21) and the stranded-reply guard (B26) -- must decide the same after the switch, apart from
the one policy change the operator ruled on (P-1, ruling 1791194105933-54bcb728, P0 C3): a verified open
turn holds delivery with NO wall-clock cap.

So a specimen here is a set of FACTS about one turn, never a row. An adapter turns the facts into whatever
the service reads. Today that is `LegacyTurnAdapter` (agent_turn_state plus the owning bridge row). After
the switch it is a mirror adapter that publishes the same facts as the C1 record aify-env would send, and
declares that it is judged under the P-1 law. The witnesses themselves do not change.

THE P-1 DIFFERENCE IS NAMED, NOT HIDDEN. `HOLD_LAW` carries both answers for every specimen. Where today's
code releases a turn (a timeout, or a future start it rejects) and P-1 would keep holding, the two columns
differ and the specimen says why; the witnesses assert the column their adapter is judged by. No
specimen's times were chosen to make the two laws agree.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Optional, Protocol

#: The strict window both laws share for a turn nothing can verify (P0 C3: "the strict legacy timer from the
#: original start, without renewal or a new anchor"), in seconds. A LITERAL, not the service's constant: the
#: specimens' ages and the P-1 oracle are derived from it, so a change to the subject's timer must turn the
#: witnesses red, never retune them. `test_p7_witness_delivery_hold` asserts the subject still agrees.
STRICT = 1800
HOUR = 3600


@dataclass(frozen=True)
class TurnSpecimen:
    """One turn, described by the facts the delivery decisions depend on.

    `recorded`        False: nothing was ever reported for this agent.
    `open`            a turn has started and no turn-end has been applied.
    `started_age`     seconds since the turn began (its anchor); None = no anchor was recorded. A negative
                      age is a time in the FUTURE: a usable stamp from a clock ahead of ours.
    `touched_age`     seconds since anything last said the turn was still going; negative = future.
    `stamps`          "valid", or "blank" / "garbage" when the times reported are unusable.
    `owner`           who claims the turn: "hook" (a runtime hook, nothing checkable), "live-bridge" (a
                      delivery bridge of this agent that is heartbeating), "dead-bridge" (one that stopped).
    `run_id`          the delivery run the turn is attributed to ('' = none).
    `verified`        the owning lifetime's process is running with identity `yes` (P0 C4). Today's code
                      never reads it; it is the fact the P-1 law decides on.
    """

    name: str
    recorded: bool = True
    open: bool = True
    started_age: Optional[float] = 60
    touched_age: Optional[float] = 60
    stamps: str = "valid"
    owner: str = "hook"
    run_id: str = ""
    verified: bool = True

    def closed(self) -> "TurnSpecimen":
        """The same turn with a turn-end applied: the eligible twin of every held case."""
        return replace(self, name=f"{self.name}/closed", open=False)


def p1_holds(spec: TurnSpecimen) -> bool:
    """The P-1 ruling, read straight from its text (P0 C3 "Busy"), for checking the table below.

    A closed or absent turn never holds. An event with no usable timestamp is refused (`no-timestamp`), so
    there is no turn. An open turn whose lifetime is verified holds with no wall-clock cap; one that cannot be
    verified uses the strict timer from its original start, never renewed.
    """
    if not (spec.recorded and spec.open) or spec.stamps != "valid":
        return False
    if spec.verified:
        return True
    anchor = spec.started_age if spec.started_age is not None else spec.touched_age
    return anchor is not None and 0 <= anchor <= STRICT


@dataclass(frozen=True)
class HoldCase:
    """A specimen and the delivery hold it must produce, under each law."""

    spec: TurnSpecimen
    legacy: bool
    p1: bool
    why: str

    def expected(self, law: str) -> bool:
        return {"legacy": self.legacy, "p1": self.p1}[law]


#: Every turn the hold (`_turn_busy_holds_delivery` today, C6 `holdsDelivery` after) is witnessed on.
HOLD_LAW = (
    HoldCase(TurnSpecimen("fresh-hook-turn"), True, True,
             "an ordinary turn in progress"),
    HoldCase(TurnSpecimen("closed-turn", open=False), False, False,
             "a turn-end was applied"),
    HoldCase(TurnSpecimen("never-reported", recorded=False), False, False,
             "nothing was ever reported"),
    HoldCase(TurnSpecimen("long-hook-turn-kept-warm", started_age=STRICT + 60, touched_age=5), False, True,
             "P-1 CHANGES THIS. Today a hook re-stamping the turn cannot extend it past the strict window from "
             "its start, so delivery is released. Under P-1 the lifetime is verified, so the open turn holds."),
    HoldCase(TurnSpecimen("long-hook-turn-unverified", started_age=STRICT + 60, touched_age=5, verified=False),
             False, False,
             "the same turn when its lifetime cannot be verified: strict from the start under both laws"),
    HoldCase(TurnSpecimen("renewed-long-turn", started_age=12 * HOUR, touched_age=5, owner="live-bridge"),
             True, True,
             "renewed five seconds ago by a live bridge of its own, twelve hours in"),
    HoldCase(TurnSpecimen("renewer-went-quiet", started_age=HOUR, touched_age=STRICT + 60, owner="live-bridge"),
             False, True,
             "P-1 CHANGES THIS. Today a live bridge that stopped renewing ages out at the strict window from "
             "its last renewal. Under P-1 the open turn of a verified lifetime holds without a renewal."),
    HoldCase(TurnSpecimen("publication-lost-inside-strict", started_age=600, touched_age=5, owner="dead-bridge",
                          verified=False), True, True,
             "the renewer is gone: nothing verifies the turn, so it holds only inside the strict window"),
    HoldCase(TurnSpecimen("publication-lost-past-strict", started_age=STRICT + 60, touched_age=5,
                          owner="dead-bridge", verified=False), False, False,
             "the renewer is gone and the strict window from the start has passed"),
    HoldCase(TurnSpecimen("unanchored-row", started_age=None, touched_age=5), True, True,
             "a row from before the start anchor existed falls back to its last touch, never to release"),
    HoldCase(TurnSpecimen("blank-stamps", stamps="blank"), False, False,
             "no usable time: a corrupt report, not a running turn (P0 C3 refuses it as no-timestamp)"),
    HoldCase(TurnSpecimen("garbage-stamps", stamps="garbage"), False, False,
             "an unparseable time is the same as none"),
    HoldCase(TurnSpecimen("verified-turn-future-start", started_age=-600, touched_age=-600), False, True,
             "P-1 CHANGES THIS. Today a start in the future gives a negative age, which the timer law rejects "
             "(turn_liveness_policy: `0 <= age`), so delivery is released. Under P-1 the stamp is usable, the "
             "lifetime is verified and the turn is open, so it holds; age is never consulted (P0 C3). Distinct "
             "from the unusable stamps above, which are refused as no-timestamp under both laws."),
    HoldCase(TurnSpecimen("future-touch-past-start", started_age=60, touched_age=-600, owner="live-bridge"),
             True, True,
             "a renewal stamped in the future is not a renewal: the verified lease discards it and falls back "
             "to the start anchor, sixty seconds ago, so the turn holds"),
)

#: The specimens on which the P-1 ruling deliberately changes today's answer. Pinned so a column cannot be
#: edited quietly: the operator approved exactly this difference.
P1_CHANGES = frozenset({"long-hook-turn-kept-warm", "renewer-went-quiet", "verified-turn-future-start"})


class TurnInputAdapter(Protocol):
    """How a specimen reaches the service. `LAW` names the column of `HOLD_LAW` the result is judged by."""

    LAW: str

    def apply(self, db_path, agent_id: str, spec: TurnSpecimen) -> None: ...


def _stamp(age: Optional[float], stamps: str) -> str:
    if stamps == "blank" or age is None:
        return ""
    if stamps == "garbage":
        return "not-a-timestamp"
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))


class LegacyTurnAdapter:
    """Today's input path: the shared `agent_turn_state` row and, for a bridge-owned turn, its bridge row.

    The hook owner is written as the marker the prompt hook really writes (`user-prompt-submit`), which
    nothing can verify. A bridge owner gets its own `bridge_instances` row, separate from any bridge that
    claims work, so a claim's heartbeat can never renew the turn by accident.
    """

    LAW = "legacy"

    def apply(self, db_path, agent_id: str, spec: TurnSpecimen) -> None:
        if not spec.recorded:
            return
        owner = {"hook": "user-prompt-submit", "live-bridge": f"turn-owner-{agent_id}",
                 "dead-bridge": f"turn-owner-{agent_id}"}[spec.owner]
        conn = sqlite3.connect(str(db_path))
        try:
            if spec.owner != "hook":
                seen = _stamp(5 if spec.owner == "live-bridge" else 3 * HOUR, "valid")
                conn.execute(
                    "INSERT OR REPLACE INTO bridge_instances (id, agent_id, registered_at, last_seen, superseded_by) "
                    "VALUES (?,?,?,?, '')", (owner, agent_id, seen, seen))
            conn.execute(
                "INSERT OR REPLACE INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, "
                "turn_runtime, turn_updated_at, turn_started_at) VALUES (?,?,?,?,?,?,?)",
                (agent_id, 1 if spec.open else 0, spec.run_id, owner, "",
                 _stamp(spec.touched_age, spec.stamps), _stamp(spec.started_age, spec.stamps)))
            conn.commit()
        finally:
            conn.close()


# THE SEAM P7 FILLS. A mirror adapter publishes each specimen as the C1 record aify-env would send (through
# the C5 receiver, with `verified` as `process.verified` and the run id as comms' own delivery turn), and
# declares LAW = "p1". It replaces this one line; no witness changes.
INPUT_ADAPTER: TurnInputAdapter = LegacyTurnAdapter()
