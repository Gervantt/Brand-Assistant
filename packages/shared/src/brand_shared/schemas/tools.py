"""MCP tool outputs (the JSON contract between the MCP server and the gateway)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from brand_shared.schemas.clients import ClientProfile
from brand_shared.schemas.content import ContentPlan, DesignerBrief, Post


class BrandbookHit(BaseModel):
    document: str
    section: str = ""
    text: str
    score: float = Field(description="Relevance in [0, 1]")


class SearchBrandbookResult(BaseModel):
    query: str
    hits: list[BrandbookHit]
    confidence: float = Field(description="Retrieval confidence in [0, 1]")
    found: bool = Field(description="False when nothing relevant is in the brand book")


class ClientProfileResult(BaseModel):
    client_id: uuid.UUID
    name: str
    profile: ClientProfile


class ContentPlanDraft(BaseModel):
    draft_id: str
    plan: ContentPlan


class PostResult(BaseModel):
    post: Post


class DesignerBriefResult(BaseModel):
    brief: DesignerBrief


class PublishResult(BaseModel):
    plan_id: uuid.UUID
    title: str
    items: int
    approved_at: datetime
    already_published: bool = False
