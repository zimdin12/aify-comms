"""Pydantic models for aify-comms API."""
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _normalize_machine_id_value(value: Optional[str]) -> Optional[str]:
    """Canonicalize machineId at request-parse ingress.

    The host machine_id is "<platform>:<hostname>" (e.g. "win32:StevenZ-L").
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


class DispatchControlUpdate(BaseModel):
    status: str
    response: Optional[str] = None


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


class SpawnRequestClaim(_MachineIdNormalizingModel):
    environmentId: str
    bridgeId: str
    machineId: Optional[str] = None


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
    runtime: Optional[str] = "pi"
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
