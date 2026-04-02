"""Pydantic models for aify-claude API."""
from typing import Optional
from pydantic import BaseModel


class AgentRegister(BaseModel):
    agentId: str
    role: str
    name: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    status: Optional[str] = None


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


class ChannelJoin(BaseModel):
    agentId: str
