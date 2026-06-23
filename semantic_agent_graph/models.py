from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class Event(BaseModel):
    seq: Optional[int] = Field(default=None, description="Monotonically increasing sequence number assigned by the database")
    id: str = Field(..., description="Unique event identifier")
    type: str = Field(..., description="Type of the event (e.g., 'object.created', 'llm.requested')")
    actor: Optional[str] = Field(default=None, description="The actor performing the event (e.g., 'agent', 'tool', 'system')")
    payload: Dict[str, Any] = Field(..., description="Event-specific metadata and parameters")
    frame_id: Optional[str] = Field(default=None, description="Optional frame context identifier for concurrent/sub-context isolation")
    caused_by: Optional[str] = Field(default=None, description="Optional ID of the event that triggered this event")
    timestamp: str = Field(..., description="ISO 8601 timestamp string representing when the event occurred")
    run_id: str = Field(..., description="The ID of the run this event belongs to")


class Run(BaseModel):
    run_id: str = Field(..., description="Unique identifier for the run")
    parent_run_id: Optional[str] = Field(default=None, description="The parent run ID if this run was forked")
    forked_at_event_id: Optional[str] = Field(default=None, description="The event ID in the parent run where this run was forked")
    label: Optional[str] = Field(default=None, description="A human-readable label or name for the run")
    created_at: str = Field(..., description="ISO 8601 timestamp string representing when the run was created")
    goal: Optional[str] = Field(default=None, description="The high-level goal or task instruction for the run")
    frame_id: Optional[str] = Field(default=None, description="Optional root frame ID for the run")


class Entity(BaseModel):
    id: str = Field(..., description="Unique identifier for the semantic entity")
    type: str = Field(..., description="The type or label of the entity (e.g., 'System', 'Variable', 'ErrorCode')")
    name: str = Field(..., description="The name/value of the entity")
    data: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary properties/metadata associated with the entity")


class Relation(BaseModel):
    id: str = Field(..., description="Unique identifier for the relationship")
    type: str = Field(..., description="The type/label of relation (e.g., 'DEPENDS_ON', 'PART_OF', 'PROCESSED')")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    data: Dict[str, Any] = Field(default_factory=dict, description="Properties associated with the relationship")
