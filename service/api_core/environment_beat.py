"""What an environment heartbeat SAYS, read once and normalised before any row is touched.

MOVED FROM `service/routers/environments.py` (audit item S2): the router held this behaviour, and a rule
that lives in a router is only reachable through the running app. The heartbeat's transaction is in
`service/api_core/environment_heartbeat.py`; this module is everything a beat can be judged on without a
database -- the id it names, what each field means when it is omitted, which keys only a bridge may
write, and whether the sender is the host tier.

`EnvironmentBeat.from_request` runs BEFORE the connection is opened, exactly as the same lines did
inside the handler, so `now` is taken at the same point it always was.

The logger keeps the router's name so a refactor does not silently move somebody's log filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _parsed_timestamp
from service.clock import ISO_SECONDS
from service.clock import now as _now
from service.env_status import BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS
from service.env_status import ENVIRONMENT_REGISTRABLE_STATUSES
from service.models import EnvironmentHeartbeat

logger = logging.getLogger("aify_comms.routers.environments")


def _canonical_runtimes(rows: Any) -> list:
    """Runtime rows with their names put through the shared vocabulary.

    THE SERVICE OWNS THE VOCABULARY, and this is the half it was not doing. A host sends the names it
    can see on disk -- `claude`, `omp` -- because `service/contracts/vocabulary.json` already maps them
    in both languages with an agreement test per side, and a second copy of that map in the environment
    tier is exactly the drift the contract exists to prevent.

    NOT A CORRECTNESS FIX. Both readers of these rows normalise both sides already, so a stored
    `claude` matches a lookup for `claude-code`. What it fixes is a row that reads `claude` while every
    agent on it reads `claude-code` -- two screens that agree only if you know the alias table.

    Idempotent: `claude-code` maps to itself, so a bridge sending canonical names is unaffected. A row
    that is not a dict is passed through rather than dropped, because inventing a shape is worse than
    storing an odd one, and the readers all use `.get`.
    """
    if not isinstance(rows, list):
        return rows
    canonical = []
    for row in rows:
        if not isinstance(row, dict):
            canonical.append(row)
            continue
        name = _normalize_runtime(row.get("runtime"))
        canonical.append({**row, "runtime": name} if name else row)
    return canonical


def _derived_environment_id(kind: Any, hostname: Any) -> str:
    """`kind:hostname:default`, the id a caller may omit.

    ONE IMPLEMENTATION, HERE. The join keys this service's own table, and a second advertiser that
    built the same string itself would agree until either copy of the rule changed -- at which point
    it would not fail, it would create a DUPLICATE environment beside the real one and leave the
    managed agents bound to whichever the bridge wrote.

    `kind` is host knowledge the service cannot compute: it distinguishes wsl, docker, windows,
    macos and linux from environment variables and `/.dockerenv` on the host itself. So the host
    sends the two facts and the service performs the join.

    THE HOSTNAME IS NOT LOWERCASED, and that is inherited rather than chosen. The live row is
    `windows:StevenZ-L:default` while its `machineId` is `win32:stevenz-l` -- the service normalises
    machineId with a field validator and has never normalised this. Lowercasing here would mint a
    new id for every existing environment and orphan the agents bound to the old one.

    Returns "" when either fact is missing, so the caller's own "id is required" refusal still fires
    rather than a half-built id like `windows::default` reaching the table.
    """
    kind_text = str(kind or "").strip()
    host_text = str(hostname or "").strip()
    if not kind_text or not host_text:
        return ""
    return f"{kind_text}:{host_text}:default"


#: What only a BRIDGE can know about itself, and therefore what an advertisement must leave alone.
#:
#: An environment-tier heartbeat describes the HOST -- runtimes, roots, terminal availability -- and
#: declares no `bridgeId`. Its metadata carries no `bridgeStartedAt` either, and `next_metadata`
#: REPLACES the stored metadata, so an advertisement erased the timestamp the supersede arbitration
#: reads. Preserving `bridge_id` alone was not enough: with two ids and no start times, the branch
#: that refuses an OLDER incoming bridge cannot fire, and a stale bridge reclaims the environment a
#: fresh one owns.
#:
#: DERIVED FROM ITS READERS, not guessed: `bridgeStartedAt` is read by `_bridge_started_at` here and
#: by `environment_claim.py`, and nothing else in the service reads a `bridge*` metadata key. A
#: second one belongs in this tuple the day it gets a reader.
#: The metadata keys only a BRIDGE can answer for, DERIVED by prefix rather than listed.
#:
#: A list of two was here, and it was one short: `bridgeBuild` rides in the same blob, `next_metadata`
#: replaces the blob, and aify-env's advertisement beats every 30s -- so a bridge's reported build was
#: erased within half a minute of being written, and `bridge-current` went back to reporting no
#: evidence. That check exists to answer "is a running bridge executing old code", which nothing else
#: can, and the cutover silently disabled it.
#:
#: `bridge*` is the honest rule: aify-env is not a bridge and sends no key by that name, so a prefix
#: match cannot over-claim, and a key added to the bridge's payload later is covered without anyone
#: remembering this line. `BRIDGE_OWNED_METADATA` stays as the explicit floor for the two that must be
#: preserved even if the prefix convention is ever broken.
BRIDGE_OWNED_METADATA = ("bridgeStartedAt", "bridgeLastSeen", "bridgeBuild")


def _bridge_owned_metadata_keys(existing_metadata) -> tuple:
    """Every key in the stored metadata that only a bridge could have written.

    Prefix-derived, with the named floor unioned in so a rename upstream cannot silently drop one.
    """
    stored = tuple(
        key for key in (existing_metadata or {})
        if isinstance(key, str) and key.startswith("bridge")
    )
    return tuple(dict.fromkeys(BRIDGE_OWNED_METADATA + stored))

#: What only the HOST can answer, and therefore what a caller describing no host must not erase.
#:
#: These three ride inside `metadata` rather than in columns, and `next_metadata` replaces the blob --
#: so the preservation rule the nine columns got did not reach them. Since the 2026-08-30 cutover the
#: aify-comms bridge stands down from describing a host whenever aify-env is advertising it, and its
#: beat carries none of the three; without this, standing down empties the terminal answer instead of
#: leaving it to the tier that owns it.
#:
#: Each is paired with the REQUEST FIELD that produces it, because "the caller said nothing about the
#: terminal" is the condition, and the metadata key is only where the answer is kept.
#: EVERY OMITTABLE-AND-PRESERVED field, WITH THE CARRIER IT TRAVELS IN. "Host-owned" was the
#: original framing and it was too narrow: the class is any field whose ABSENCE means "keep what
#: is stored", whichever tier supplies it. One declaration, because there are two
#: carriers and splitting them is how a field goes missing: three of these live in the `metadata`
#: blob and TWO are COLUMNS, so a set that named only the metadata keys looked complete while
#: describing three fifths of the problem.
#:
#: DERIVED-AGAINST, NOT MERELY WRITTEN DOWN. This is still a literal, because a field's CARRIER
#: cannot be inferred from the model -- nothing in `runtimes: Optional[list]` says it is a column.
#: What makes it safe is the gate: `test_every_host_owned_field_is_declared.py` reads the handler for
#: the `if req.X is not None` guards that mark a field as host-describing, and fails if any of them is
#: absent here or if anything here is absent there. Adding a fifth host fact without declaring it is
#: a RED test rather than a silent erasure on the next advertisement.
#:
#: The gate exists because the erasure already happened twice: `50a61dbe` stopped a heartbeat blanking
#: what it did not mention, and `badab14c` then found FOUR MORE fields it had missed. A hand-kept list
#: with no gate is that same defect with a delay on it, and the bridge-owned set thirty lines above
#: this one was already derived for exactly that reason.
METADATA_CARRIER = "metadata"
COLUMN_CARRIER = "column"

HOST_OWNED_FIELDS = (
    ("terminal", METADATA_CARRIER, "terminal"),
    ("pty", METADATA_CARRIER, "pty"),
    ("terminalRuntimes", METADATA_CARRIER, "terminalRuntimes"),
    #: The two that are not metadata. Preserved by their own `is None` restores below rather than by
    #: the metadata loop, which is why a metadata-only list could never have caught their absence.
    #: Their declared key is the COLUMN NAME, and `test_each_declared_host_field_survives_omission`
    #: reads it straight out of `environments` -- so a key that is not a column fails rather than
    #: quietly reading something else.
    ("runtimes", COLUMN_CARRIER, "runtimes"),
    #: FOUND BY THE GATE ON ITS FIRST RUN, which is the argument for having one. `cwdRoots` is
    #: supplied by the BRIDGE rather than described by the host -- aify-env sends none by design -- so
    #: it is not a host fact. It is in this set anyway because the set is about the ERASURE CLASS, not
    #: about which tier speaks: a field whose absence means "keep what is stored" is blanked the
    #: moment nothing preserves it, and `cwd_roots` is restored at the `cwd_roots is None` branch
    #: exactly as `runtimes` is. Naming it here is what stops the next reader assuming the column half
    #: is a single special case.
    ("cwdRoots", COLUMN_CARRIER, "cwd_roots"),
)

#: The metadata half, derived rather than repeated. Its consumers are unchanged.
HOST_OWNED_METADATA = tuple(
    (field, key) for field, carrier, key in HOST_OWNED_FIELDS if carrier == METADATA_CARRIER
)


def _bridge_started_at(metadata: Any) -> str:
    """When the sending bridge says it started, as stored.

    `_parsed_timestamp`: this value comes from the registering bridge and decides whether a FORGOTTEN
    environment may come back. A sort key keeps an unparseable string as itself and letters sort above
    digits, so "now" beat every real `forgottenAt`. Same hole as the agent tombstone gate, in the
    guard that explicitly says it "mirrors" it -- both fixed together.

    THE CEILING IS APPLIED WHERE THE VALUE IS WRITTEN, not here. External review, Round 8 M1: a
    `bridgeStartedAt` in the future outranked every real bridge until the clock caught up with it,
    which needs no bad actor -- this project has measured a container clock 4.1 seconds ahead of its
    host, and a machine with a badly set clock sends a start time days out.

    A READER IS THE WRONG PLACE FOR IT, and both of my first two attempts proved it. A ceiling
    evaluated on every read MOVES WITH THE CLOCK, so a poisoned row reads as "now" for ever and still
    outranks every honestly-timed bridge -- "holds the row until 2099" becomes "holds it permanently".
    And a ceiling of exactly `now` here pulled a genuinely fresh relaunch back below the `forgottenAt`
    it had to beat, so a forgotten environment could not be brought back; `test_lifecycle_phase7`
    caught that within the same run.

    Bounded on the way IN, once, against the clock at that moment, a poisoned row also repairs itself:
    the next heartbeat from that bridge carries its own start time and replaces the stored one. So no
    migration is needed here, and this reader stays exactly what it was.
    """
    if isinstance(metadata, dict):
        return _parsed_timestamp(metadata.get("bridgeStartedAt"))
    return ""


#: What a host-tier claimer calls itself. `aify-env` sends this in `metadata.bridgeKind`; a legacy
#: aify-comms environment bridge sends nothing, which is exactly what makes the absence meaningful.
HOST_TIER_BRIDGE_KIND = "aify-env"


def _is_host_tier(metadata: Any) -> bool:
    """Whether this beat comes from the HOST TIER rather than a legacy environment bridge.

    ABSENT MEANS LEGACY, and that is the whole reason this reads a positive marker rather than a
    version. Every pre-0.6.2 sender is silent here, so a missing value is a fact about the sender and
    not a gap in the data.
    """
    if isinstance(metadata, dict):
        return str(metadata.get("bridgeKind") or "").strip().lower() == HOST_TIER_BRIDGE_KIND
    return False


def _normalize_roots(roots: Optional[list[str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for root in roots or []:
        value = str(root or "").strip()
        if not value or value.startswith("-"):
            continue
        key = value.replace("\\", "/").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result



@dataclass(frozen=True)
class EnvironmentBeat:
    """One heartbeat, as the service reads it: the request plus what was derived from it.

    `req` travels with it because the registration write and the superseded-bridge stop read the
    request's own fields. `metadata` is the request's metadata dict with `heldTerminals` removed and
    the host facts written in -- the same object the handler used to mutate in place.
    """

    env_id: str
    req: EnvironmentHeartbeat
    now: str
    cwd_roots: Optional[list]
    runtimes: Any
    metadata: Any
    held_terminals: Any
    requested_status: str

    @classmethod
    def from_request(cls, req: EnvironmentHeartbeat, env_id: str) -> "EnvironmentBeat":
        now = _now()
        #: `is None`, NOT falsiness. For a list, "said nothing" and "said there are none" are different
        #: claims, and `or []` collapsed them -- so a heartbeat that omitted either field erased it. The
        #: stored value is restored below, once `existing` has been read.
        cwd_roots = _normalize_roots(req.cwdRoots) if req.cwdRoots is not None else None
        runtimes = _canonical_runtimes(req.runtimes) if req.runtimes is not None else None
        metadata = req.metadata or {}
        #: WHICH TERMINALS THE HOST HOLDS is an observation of this one beat, read here and never stored:
        #: kept in the row's metadata it would read as current long after the host that sent it had gone.
        held_terminals = metadata.pop("heldTerminals", None) if isinstance(metadata, dict) else None
        # A START TIME IN THE FUTURE IS BOUNDED ON THE WAY IN, once, against the clock as it is now.
        # External review, Round 8 M1: arbitration prefers the LATER start time and nothing bounded how
        # late, so a value in the future outranked every correctly clocked bridge until real time caught
        # up with it. Bounding it at READ time was tried and is worse -- see `_bridge_started_at`.
        if isinstance(metadata, dict) and metadata.get("bridgeStartedAt"):
            _claimed_start = _parsed_timestamp(metadata.get("bridgeStartedAt"))
            # PAST THE SKEW TOLERANCE, not past `now`. This clamped at exactly now until a test caught
            # it, and a zero-tolerance future check is a shape this repo has already paid for: doctor's
            # `env-bridge` read a container clock 4.1 seconds ahead of its host and reported every fresh
            # heartbeat as bogus. An ordinary host a few seconds out is not making a claim about the
            # future; it is a host with a clock.
            #
            # `BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS` is the number this service already uses for exactly
            # this question in `env_status.py`, so the two answers cannot drift apart. A second constant
            # here would be a rule to keep in step, which is a defect with a delay on it.
            _ceiling = _parsed_timestamp(
                (datetime.now(timezone.utc)
                 + timedelta(seconds=BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS)).strftime(ISO_SECONDS)
            )
            if _claimed_start and _ceiling and _claimed_start > _ceiling:
                logger.info(
                    "environment %s: bridge %s reported starting at %s, which is in the future; "
                    "recording %s instead. Check that host's clock.",
                    env_id, str(req.bridgeId or "") or "(none)", metadata.get("bridgeStartedAt"), _ceiling,
                )
                metadata = {**metadata, "bridgeStartedAt": _ceiling}
        if req.terminal is not None:
            metadata["terminal"] = bool(req.terminal)
        if req.pty is not None:
            metadata["pty"] = bool(req.pty)
        if req.terminalRuntimes is not None:
            metadata["terminalRuntimes"] = [
                _normalize_runtime(str(runtime or ""))
                for runtime in req.terminalRuntimes
                if str(runtime or "").strip()
            ]
        requested_status = str(req.status or "online").strip().lower()
        if requested_status not in ENVIRONMENT_REGISTRABLE_STATUSES:
            requested_status = "online"
        return cls(
            env_id=env_id, req=req, now=now, cwd_roots=cwd_roots, runtimes=runtimes, metadata=metadata,
            held_terminals=held_terminals, requested_status=requested_status,
        )
