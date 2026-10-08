"""Pydantic request/response models (the API contract)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# --- Requests --------------------------------------------------------------- #
class AuthRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=6, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class MergeRequest(BaseModel):
    keep_id: str
    dup_id: str


# --- Sub-objects ------------------------------------------------------------ #
class EvidenceItem(BaseModel):
    id: str
    message_id: str
    quote: str
    created_at: str


class NodeOut(BaseModel):
    id: str
    conversation_id: str
    dimension: str
    key: str
    value: str
    confidence_raw: float
    confidence_calibrated: float
    valid_from: str
    valid_to: Optional[str]
    status: str
    status_detail: Optional[str] = "active"
    previous_node_id: Optional[str]
    created_at: str


class NodeDetail(NodeOut):
    evidence: list[EvidenceItem] = []
    history: list[NodeOut] = []  # supersession chain, newest first


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    salience_score: Optional[float] = None
    salient: bool = False


# --- Responses -------------------------------------------------------------- #
class TurnChange(BaseModel):
    action: str  # created | superseded | reinforced | ended | ignored
    node_id: Optional[str] = None
    dimension: str
    key: str
    value: str
    previous_node_id: Optional[str] = None
    confidence_calibrated: Optional[float] = None
    reason_code: Optional[str] = None
    matched_label: Optional[str] = None
    similarity: Optional[float] = None


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    reply: str
    salient: bool
    salience_score: float
    changes: list[TurnChange] = []
    latency_ms: int = 0


class GraphNode(BaseModel):
    data: dict


class GraphEdge(BaseModel):
    data: dict


class GraphResponse(BaseModel):
    user_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class UserOut(BaseModel):
    id: str
    username: Optional[str] = None
    label: Optional[str]
    created_at: str


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class SessionOut(BaseModel):
    id: str
    user_id: str
    title: Optional[str]
    created_at: str
    closed_at: Optional[str] = None
