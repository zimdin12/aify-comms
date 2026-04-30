"""Pydantic models for aify-comms API."""
from typing import Any, Literal, Optional
from pydantic import BaseModel


class AgentRegister(BaseModel):
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
    autoRegister: Optional[bool] = False
    restoreDeleted: Optional[bool] = False


class AgentDescribeRequest(BaseModel):
    description: str


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


class DispatchClaimRequest(BaseModel):
    agentId: str
    machineId: Optional[str] = None
    bridgeId: Optional[str] = None
    executionModes: Optional[list[str]] = None


class DispatchRunUpdate(BaseModel):
    status: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    resultMessageId: Optional[str] = None
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


class DispatchControlClaimRequest(BaseModel):
    agentId: str
    runId: Optional[str] = None
    machineId: Optional[str] = None


class DispatchControlUpdate(BaseModel):
    status: str
    response: Optional[str] = None


class EnvironmentHeartbeat(BaseModel):
    id: str
    label: Optional[str] = None
    machineId: Optional[str] = None
    os: Optional[str] = None
    kind: Optional[str] = None
    bridgeId: Optional[str] = None
    bridgeVersion: Optional[str] = None
    cwdRoots: Optional[list[str]] = None
    runtimes: Optional[list[dict[str, Any]]] = None
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class EnvironmentControlRequest(BaseModel):
    action: str
    requestedBy: Optional[str] = None


class EnvironmentRootsUpdate(BaseModel):
    roots: Optional[list[str]] = None
    requestedBy: Optional[str] = None
    resetToBridgeAdvertised: Optional[bool] = False


class EnvironmentControlClaim(BaseModel):
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


class SpawnRequestClaim(BaseModel):
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


class AgentControlRequest(BaseModel):
    action: str
    from_agent: Optional[str] = None
    body: Optional[str] = None


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
