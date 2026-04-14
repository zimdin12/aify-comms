"""Pydantic models for aify-comms API."""
from typing import Any, Optional
from pydantic import BaseModel


class AgentRegister(BaseModel):
    agentId: str
    role: str
    name: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
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


class AgentRuntimeStateUpdate(BaseModel):
    runtimeState: dict[str, Any]


class SpawnAgentRequest(BaseModel):
    from_agent: str
    agentId: str
    role: str
    runtime: str
    name: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    machineId: Optional[str] = None
    priority: str = "normal"
    subject: Optional[str] = None
    body: Optional[str] = None
    runtimeConfig: Optional[dict[str, Any]] = None


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
    createMessage: bool = True
    requestedRuntime: Optional[str] = None


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


class ChannelJoin(BaseModel):
    agentId: str
