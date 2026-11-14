"""Public metadata shapes; internal snapshots and unpublished text stay private."""

from typing import Literal

from pydantic import BaseModel, Field


class GroupExpertResponse[T](BaseModel):
    data: T


class ExpertSourceIdentity(BaseModel):
    source_agent_id: str
    source_row_id: int


class ExpertAvatar(BaseModel):
    url: str | None = None


class ExpertSource(ExpertSourceIdentity):
    display_name: str
    avatar: ExpertAvatar
    can_call: bool = False


class ExpertBinding(ExpertSource):
    binding_id: str
    conversation_id: str
    generation: int
    scope_revision: int
    joined_seq: int
    state: Literal["active", "removed", "invalidated"]


class ExpertListing(BaseModel):
    items: list[ExpertBinding]
    candidates: list[ExpertSource]
    scope_sources: list[ExpertSourceIdentity]
    scope_revision: int
    experts_enabled: bool
    ready: bool
    syncing: bool
    can_manage: bool
    can_configure: bool
    status: str
    group_kind: str
    department_id: str | None


class ExpertCandidates(BaseModel):
    items: list[ExpertBinding]


class ExpertInvitationResult(BaseModel):
    binding: ExpertBinding


class ExpertRemovalResult(BaseModel):
    binding_id: str
    generation: int
    state: str


class ExpertSyncStatus(BaseModel):
    syncing: bool = Field(
        description="True until authority and binding changes have durable Go receipts."
    )


class ExpertScopeResult(ExpertSyncStatus):
    revision: int


class ExpertEnableResult(BaseModel):
    experts_enabled: bool


class ExpertCapabilities(BaseModel):
    protocol: int
    schema_version: int = Field(alias="schema")
    mode: str
    ready: bool


class ExpertAdmission(BaseModel):
    call_id: str
    admission_state: Literal["accepted", "rejected", "cancelled"]
    reason_code: str
    run_id: str | None


class ExpertCallMetadata(ExpertAdmission):
    execution_state: str | None
    delivery_state: Literal["none", "pending", "confirmed", "unpublished"]
    result_digest: str | None


class ExpertControlReceipt(BaseModel):
    call_id: str
    saved: bool
    stopped: bool = Field(
        description="A saved cancellation alone is not proof the execution stopped."
    )
    execution_state: str | None


class ExpertDeliveryReceipt(BaseModel):
    call_id: str
    result_digest: str
    call_state: str
    delivery_state: Literal["confirmed", "unpublished"]
    reason_code: str = ""
    message_id: str | None = None
    seq: str | None = None


class ExpertDeliveryResult(BaseModel):
    saved: bool
    receipt: ExpertDeliveryReceipt | None = None
