"""Pydantic models for aify-comms API."""
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _normalize_machine_id_value(value: Optional[str]) -> Optional[str]:
    """Canonicalize machineId at request-parse ingress.

    The host machine_id is "<platform>:<hostname>" (e.g. "win32:DevBox-1").
    Different launch paths report the hostname with different casing, and the
    service compares machine_id case-sensitively in bridge supersession and
    dispatch-claim routing. Lowercasing here (platform is already lowercase;
    only the host casing varies) makes every downstream store/compare site
    receive a consistent value. Idempotent and safe. None passes through so
    "unset" stays distinct from "empty".
    """
    if value is None:
        return None
    return str(value).strip().lower()


class _MachineIdNormalizingModel(BaseModel):
    """Base for request models carrying a machineId that must be lowercased."""

    @field_validator("machineId", check_fields=False)
    @classmethod
    def _normalize_machine_id(cls, value):
        return _normalize_machine_id_value(value)


# A model NAME is passed to a runtime CLI as an argument. It is not validated against a list of
# real models on purpose — see below — but it is validated for SHAPE, because the shape errors are
# the ones that produce a worker nobody can diagnose.
#
# THE ARTIFACT: `mcptest-fakemodel-claude` was spawned 2026-07-01T13:55:53Z with model
# `totally-fake-model-9000`. It sat `running` for 23 hours and was finally closed by a generic
# reaper reporting "Orphaned: claiming environment bridge is no longer live" — which was not the
# cause. The operator got a wrong diagnosis a day late for an input that was wrong on arrival.
#
# WHY NOT AN ALLOWLIST, which is the obvious answer: model names change constantly and a stale
# allowlist would reject legitimate spawns, which is worse than the bug it prevents. The deep case
# — a plausible-looking name that no provider serves — is now covered from the other end anyway:
# v0.2.0's `_finalize_spawns_with_dead_terminals` plus `terminal_diagnostics.py` surfaces the
# runtime's own first fatal line when the worker exits, which is the honest place to learn that a
# provider rejected a model.
#
# What is left for this boundary is the class an allowlist is not needed for: strings that cannot be
# a model name at all — whitespace inside, control characters, shell metacharacters, absurd length.
# Those are typos and bad pastes, they are certain rather than probable, and catching them costs one
# comparison instead of a day.
_MODEL_MAX_LEN = 120
_MODEL_FORBIDDEN = set(' \t\r\n"\'`;|&$<>(){}[]*?!\\')


def validate_runtime_config_model(value):
    """THE FIFTH DOOR, found by an external review after I called four of them a boundary.

    `runtimeConfig` is a free-form dict, and `mcp/stdio/terminal-env.js` reads
    `runtimeConfig.model` as the FALLBACK for `AIFY_MANAGED_MODEL` (and for the managed CODEX_HOME
    it prepares). So `runtimeConfig={"model": "opus; rm -rf /"}` reached a runtime CLI having passed
    none of the four validated doors — reproduced verbatim before this fix.

    That is exactly the thesis of the commit that closed the other four ("a validator on one of four
    doors is not a boundary"), applied to a door I did not enumerate. The lesson is not "add a fifth
    check" — it is that a free-form dict beside a validated scalar is a hole by construction, so the
    dict's model key now goes through the SAME rule as the scalar.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or "model" not in value:
        return value
    cleaned = dict(value)
    cleaned["model"] = _validate_model_shape(cleaned["model"])
    if cleaned["model"] is None:
        cleaned.pop("model")
    return cleaned


def drop_unusable_runtime_config_model(value):
    """Self-report variant, for the same reason `drop_unusable_model_selfreport` exists: a
    registration must never fail over a model string. An unusable value is dropped from the dict,
    leaving the rest of the config intact — the agent keeps its effort/thinking settings and simply
    reports no model."""
    if not isinstance(value, dict) or "model" not in value:
        return value
    cleaned = dict(value)
    if drop_unusable_model_selfreport(cleaned.get("model")) is None:
        cleaned.pop("model")
    else:
        cleaned["model"] = str(cleaned["model"]).strip()
    return cleaned


def drop_unusable_model_selfreport(value):
    """A FOURTH ingress, and it must not be treated like the other three.

    `AgentRegister.model` is an agent's self-report about the runtime it is already running, and it
    reaches a CLI later via `agentInfo.model` -> `AIFY_MANAGED_MODEL`. So it is a real ingress. But
    the failure modes are not symmetric:

      a REQUEST that sets policy (spawn, environment assign, settings) should be REJECTED — the
        caller is choosing something, and a 400 tells them it was not accepted;
      a SELF-REPORT must never be able to fail its own registration. An agent that cannot register
        is dead: no inbox, no dispatch, no status. Trading a whole live agent for a cosmetically
        bad model string is a worse outcome than the bug.

    So this DROPS an unusable value instead of rejecting it or repairing it. Dropping means "we do
    not know this agent's model", and the launch falls back to the runtime default. Repairing would
    be the wrong choice for the same reason `terminal_diagnostics` refuses to guess: "opus 5"
    mangled into "opus5" is a confident answer nobody can trace, while absence is honest.
    """
    if value is None:
        return None
    try:
        return _validate_model_shape(value)
    except ValueError:
        return None


def validate_model_shape(value):
    """Public because a boundary is only a boundary if EVERY ingress uses it.

    Review caught this: the first cut attached the validator to `SpawnRequestCreate` only and I
    described it as "every path gets it". `AgentEnvironmentAssignRequest(model="opus;rm")` was
    accepted verbatim and written to `spawn_specs.model`, from where the next spawn picks it up. A
    validator on one of three doors is not validation."""
    return _validate_model_shape(value)


def _validate_model_shape(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        # An empty/whitespace model means "use the runtime default" — the same as omitting it.
        return None
    if len(text) > _MODEL_MAX_LEN:
        raise ValueError(f"model name is {len(text)} characters; that is not a model name")
    bad = sorted({c for c in text if c in _MODEL_FORBIDDEN or ord(c) < 0x20})
    if bad:
        shown = ", ".join(repr(c) for c in bad)
        raise ValueError(
            f"model name contains characters a runtime CLI cannot receive as one argument ({shown}). "
            "A model name is a single token like 'opus' or 'gpt-5.5'."
        )
    return text


class AgentRegister(_MachineIdNormalizingModel):
    agentId: str
    role: str
    name: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    status: Optional[str] = None
    runtime: Optional[str] = None
    machineId: Optional[str] = None
    bridgeId: Optional[str] = None
    launchMode: Optional[str] = None
    sessionMode: Optional[str] = None
    sessionHandle: Optional[str] = None
    managedBy: Optional[str] = None
    capabilities: Optional[list[str]] = None
    runtimeConfig: Optional[dict[str, Any]] = None
    terminalId: Optional[str] = None
    managedWrapperChild: Optional[bool] = False
    autoRegister: Optional[bool] = False
    restoreDeleted: Optional[bool] = False

    # Self-report, so an unusable value is DROPPED rather than rejected — see
    # `drop_unusable_model_selfreport`. Registration must never fail on a model string: an agent
    # that cannot register has no inbox, no dispatch and no status.
    _clean_model = field_validator("model")(drop_unusable_model_selfreport)
    # Self-report, so an unusable runtimeConfig.model is DROPPED from the dict rather than failing
    # the registration — the rest of the config (effort/thinking) survives.
    _clean_runtime_config = field_validator("runtimeConfig")(drop_unusable_runtime_config_model)
    # Tombstone-resurrection guard (2026-06-03). The bridge stamps its own launch
    # time here (BRIDGE_STARTED_AT, ISO-8601 Z). A tombstoned agent is only
    # resurrected by a GENUINE fresh relaunch — a bridge whose bridgeStartedAt is
    # NEWER than the tombstone's removed_at. A passive auto re-register / heartbeat
    # from a bridge that launched BEFORE the deletion must NOT clear the tombstone.
    # Mirrors the environment forget-tombstone freshness check (forgottenAt vs
    # bridgeStartedAt) in environment_heartbeat.
    bridgeStartedAt: Optional[str] = None
    # Phase 4 race guard (2026-05-31): a fresh same-mode resident re-register
    # by a DIFFERENT bridge is hard-rejected (409) to prevent two live wrappers
    # silently racing one identity. Set force=true to take over deliberately
    # (operator restarted the prior wrapper). Wrappers surface this via the
    # AIFY_FORCE_REGISTER escape hatch.
    force: Optional[bool] = False


class AgentDescribeRequest(BaseModel):
    description: str


class AgentFavoriteUpdate(BaseModel):
    favorited: bool


class AgentStatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None


class MessageSend(BaseModel):
    from_agent: str
    to: Optional[str] = None
    toRole: Optional[str] = None
    type: str = "info"
    subject: str
    body: str
    priority: str = "normal"
    inReplyTo: Optional[str] = None
    trigger: bool = False
    steer: Optional[bool] = None
    queueIfBusy: bool = False
    requireReply: Optional[bool] = None
    clientNonce: Optional[str] = None


class AgentRuntimeStateUpdate(BaseModel):
    runtimeState: dict[str, Any]


class AgentSessionHandleUpdate(BaseModel):
    sessionHandle: Optional[str] = None
    requestedBy: Optional[str] = None


class AgentReadyUpdate(BaseModel):
    # Plan 4 task 12 (2026-05-25): set by the bridge after an adapter
    # controller's start() handshake completes. This is an internal
    # readiness bit; public idle-live status is `online`, not `ready`.
    ready: bool = True
    requestedBy: Optional[str] = None


class AgentSessionResolveRequest(BaseModel):
    # Sticky session identity (governance, 2026-05-30): operator resolution of a
    # `session-changed` state. Used by both POST /agents/{id}/session/confirm
    # (re-pin persisted := pending) and /session/keep (clear pending, keep
    # persisted, surface the resume command). No body fields are required;
    # requestedBy is optional audit metadata.
    requestedBy: Optional[str] = None


class AgentSessionModeSwitchRequest(BaseModel):
    # Plan 6 C1 (2026-05-26): operator-driven resident/managed flip.
    # `mode` must be 'resident' or 'managed'. `force=true` overrides
    # the active-run guard and the hermes-without-gateway guard.
    mode: str
    force: bool = False
    requestedBy: Optional[str] = None


class AgentResidentLostRequest(_MachineIdNormalizingModel):
    bridgeId: Optional[str] = None
    machineId: Optional[str] = None
    runtime: Optional[str] = None
    reason: Optional[str] = None


class ConversationClearRequest(BaseModel):
    agentId: str
    peerId: str


class DispatchRequest(BaseModel):
    from_agent: str
    to: Optional[str] = None
    toRole: Optional[str] = None
    type: str = "request"
    subject: str
    body: str
    priority: str = "normal"
    inReplyTo: Optional[str] = None
    mode: str = "start_if_possible"
    createMessage: Literal[True] = True
    requestedRuntime: Optional[str] = None
    steer: bool = False
    requireReply: Optional[bool] = None


class DispatchClaimRequest(_MachineIdNormalizingModel):
    agentId: str
    machineId: Optional[str] = None
    bridgeId: Optional[str] = None
    executionModes: Optional[list[str]] = None
    # Long-poll budget in ms (0 = legacy immediate return). The server holds the
    # claim open up to this long (capped at longpoll.MAX_WAIT_S) waiting for work.
    waitMs: Optional[int] = 0
    # Standalone channel sidecars (mcp/stdio/claude-channel.js,
    # hermes-channel.js) declare bridgeKind="channel-sidecar" so the claim gate
    # can distinguish them from a wrapper-PTY child (which registers as
    # bridge_kind="managed-wrapper-child"). See _bridge_claim_block_reason.
    bridgeKind: Optional[str] = None


class DispatchRunUpdate(BaseModel):
    status: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    resultMessageId: Optional[str] = None
    requireReply: Optional[bool] = None
    externalThreadId: Optional[str] = None
    externalTurnId: Optional[str] = None
    runtime: Optional[str] = None
    agentStatus: Optional[str] = None
    appendEvent: Optional[str] = None
    eventType: Optional[str] = None


class DispatchControlRequest(BaseModel):
    from_agent: Optional[str] = None
    action: str
    body: Optional[str] = None


class DispatchControlClaimRequest(_MachineIdNormalizingModel):
    agentId: str
    runId: Optional[str] = None
    machineId: Optional[str] = None
    waitMs: Optional[int] = 0  # long-poll budget (0 = legacy immediate return)


class DispatchControlUpdate(_MachineIdNormalizingModel):
    # NORMALIZING BASE, because this model's machineId is COMPARED, not just recorded: the settlement
    # is refused unless it matches the `claim_machine_id` stamped at claim time, and that value went
    # through the same normalisation. A plain BaseModel here would let a case or separator difference
    # between two derivations of the same machine id read as "a different machine claimed this" — a
    # 409 on every settlement, which leaves the control pending and strands the run.
    #
    # I wrote it as `BaseModel` first. `test_machine_id_normalisation.py` caught it by sweeping every
    # model that carries the field, which is the only reason it is not a live bug: nothing in the
    # happy path would have differed on the machine that wrote both values.
    status: str
    response: Optional[str] = None
    # WHO SETTLED THIS CONTROL, and from where. Mandatory in EFFECT but Optional in the schema, on
    # purpose: a missing field must produce the handler's own 400 — which names the cause and says
    # "relaunch" — rather than FastAPI's generic 422. This endpoint's refusal leaves the control
    # `pending` forever and strands the run, so the error text is the only place an operator learns
    # that a pre-actor bridge is the reason. See test_dispatch_control_settlement_names_its_actor.py.
    handledBy: Optional[str] = None
    machineId: Optional[str] = None


class EnvironmentHeartbeat(_MachineIdNormalizingModel):
    id: str
    label: Optional[str] = None
    machineId: Optional[str] = None
    os: Optional[str] = None
    kind: Optional[str] = None
    bridgeId: Optional[str] = None
    bridgeVersion: Optional[str] = None
    cwdRoots: Optional[list[str]] = None
    runtimes: Optional[list[dict[str, Any]]] = None
    terminal: Optional[bool] = None
    pty: Optional[bool] = None
    terminalRuntimes: Optional[list[str]] = None
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class EnvironmentControlRequest(BaseModel):
    action: str
    requestedBy: Optional[str] = None


class EnvironmentRootsUpdate(BaseModel):
    roots: Optional[list[str]] = None
    requestedBy: Optional[str] = None
    resetToBridgeAdvertised: Optional[bool] = False


class EnvironmentControlClaim(_MachineIdNormalizingModel):
    environmentId: str
    bridgeId: str
    machineId: Optional[str] = None
    waitMs: Optional[int] = 0  # long-poll budget (0 = legacy immediate return)


class EnvironmentControlUpdate(BaseModel):
    status: str
    error: Optional[str] = None


class AgentEnvironmentAssignRequest(BaseModel):
    environmentId: str
    workspace: Optional[str] = None
    runtime: Optional[str] = None
    model: Optional[str] = None
    runtimeConfig: Optional[dict[str, Any]] = None
    requestedBy: Optional[str] = None

    # The bypass review found. This path writes `model` into `spawn_specs.model`, from where the
    # next spawn reads it — so a malformed value here reaches a runtime CLI exactly as it would via
    # SpawnRequestCreate, just one hop later and harder to trace.
    _check_model = field_validator("model")(_validate_model_shape)
    _check_runtime_config = field_validator("runtimeConfig")(validate_runtime_config_model)


class AgentRenameRequest(BaseModel):
    newAgentId: str
    requestedBy: Optional[str] = None


class SpawnRequestCreate(BaseModel):
    createdBy: Optional[str] = None
    environmentId: str
    agentId: str
    role: str = "coder"
    name: Optional[str] = None
    runtime: str
    workspace: Optional[str] = None
    model: Optional[str] = None
    runtimeConfig: Optional[dict[str, Any]] = None
    profile: Optional[str] = None
    systemPrompt: Optional[str] = None
    instructions: Optional[str] = None
    initialMessage: Optional[str] = None
    priority: str = "normal"
    subject: Optional[str] = None
    mode: str = "managed-warm"
    resumePolicy: str = "native_first"
    channelIds: Optional[list[str]] = None
    envVars: Optional[dict[str, Any]] = None
    budgetPolicy: Optional[dict[str, Any]] = None
    contextPolicy: Optional[dict[str, Any]] = None
    restartPolicy: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None

    _check_model = field_validator("model")(_validate_model_shape)
    # The fifth door: runtimeConfig.model is the bridge's FALLBACK for AIFY_MANAGED_MODEL.
    _check_runtime_config = field_validator("runtimeConfig")(validate_runtime_config_model)


class SpawnRequestClaim(_MachineIdNormalizingModel):
    environmentId: str
    bridgeId: str
    machineId: Optional[str] = None
    waitMs: Optional[int] = 0  # long-poll budget (0 = legacy immediate return)


class SpawnRequestUpdate(BaseModel):
    status: str
    bridgeId: Optional[str] = None
    processId: Optional[str] = None
    sessionHandle: Optional[str] = None
    error: Optional[str] = None
    runtimeState: Optional[dict[str, Any]] = None
    capabilities: Optional[dict[str, Any]] = None
    telemetry: Optional[dict[str, Any]] = None


class SessionControlRequest(BaseModel):
    action: str
    from_agent: Optional[str] = None
    body: Optional[str] = None
    subject: Optional[str] = None
    priority: str = "normal"


class ConsoleStartRequest(BaseModel):
    requestedBy: Optional[str] = None
    workspace: Optional[str] = None
    command: Optional[str] = None
    freshContext: Optional[bool] = False


class TerminalControlRequest(BaseModel):
    requestedBy: Optional[str] = None
    body: Optional[str] = None
    cols: Optional[int] = None
    rows: Optional[int] = None


class TerminalControlClaim(BaseModel):
    environmentId: str
    bridgeId: str
    waitMs: Optional[int] = 0  # long-poll budget (0 = legacy immediate return)


class TerminalControlUpdate(BaseModel):
    status: str
    terminalStatus: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    # PTY root pid reported by the owning bridge on terminal attach (start
    # control completion). Persisted to terminal_sessions.process_id so an
    # orphaned PTY can be killed by-pid when its bridge is gone.
    processId: Optional[str] = None


class TerminalDeadReport(BaseModel):
    # Host-reported dead-PTY signal (WS4 Task 4.2). The owning environment
    # bridge is the only thing that can probe a local PID; when a console PTY's
    # process is no longer alive it POSTs this so the server can mark the row
    # stopped + invalidate live-state. `processId` (when present) must match the
    # stored process_id so a stale report can't stop a row a restarted bridge
    # now owns with a NEW pid.
    bridgeId: Optional[str] = None
    processId: Optional[str] = None
    reason: Optional[str] = None


class TerminalOutputRequest(BaseModel):
    bridgeId: Optional[str] = None
    output: Optional[str] = None
    status: Optional[str] = None


class VirtualTerminalEnsureRequest(BaseModel):
    bridgeId: str
    sessionHandle: Optional[str] = None
    workspace: Optional[str] = None
    # NO DEFAULT, deliberately. `ensure_virtual_terminal` resolves this as
    # `req.runtime or agent["runtime"] or "pi"` — a three-step chain whose middle step was DEAD
    # while the model substituted "pi" here: a caller that omitted `runtime` never reached the
    # agent's own. That is not a formatting difference. The runtime picks the sentinel command
    # written into `terminal_sessions.command`, so a codex agent would have been given
    # `aify://virtual-rpc/pi`, and it would have passed the wrapper-deprecation gate that exists to
    # refuse it — pi is never wrapper-backed, codex is. The endpoint keeps the final `or "pi"`, so
    # nothing loses a default; the agent's own runtime now gets consulted first, as the code reads.
    #
    # Latent rather than live: `mcp/stdio/virtual-terminals.mjs` returns null on an empty runtime
    # and always sends one. The dashboard and any future caller are not bound by that.
    runtime: Optional[str] = None
    requestedBy: Optional[str] = None


class AgentControlRequest(BaseModel):
    action: str
    from_agent: Optional[str] = None
    body: Optional[str] = None


class AgentConsoleInputRequest(BaseModel):
    # Text to inject into another agent's live console (e.g. a command, or an
    # empty string + enter=True to unstick a paused TUI). `from_` records the
    # caller for the audit event; resolution is agent->terminal server-side so
    # no caller can target an arbitrary terminal id.
    text: Optional[str] = None
    enter: Optional[bool] = True
    from_: Optional[str] = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


class ClearRequest(BaseModel):
    target: str  # inbox, shared, agents, all, channels
    agentId: Optional[str] = None
    olderThanHours: Optional[float] = None


class ChannelCreate(BaseModel):
    name: str
    description: Optional[str] = None
    createdBy: str


class ChannelMessage(BaseModel):
    from_agent: str
    channel: str
    body: str
    type: str = "info"
    priority: str = "normal"
    trigger: bool = True
    silent: bool = False
    steer: Optional[bool] = None
    queueIfBusy: bool = False


class ChannelJoin(BaseModel):
    agentId: str
