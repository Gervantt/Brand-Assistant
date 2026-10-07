"""Content-plan drafts live in Redis (ephemeral state); only publishing writes to Postgres."""

import json
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

from brand_shared.schemas.content import ContentPlan

PREFIX = "draft:plan:"


@dataclass(frozen=True)
class Draft:
    draft_id: str
    client_id: uuid.UUID
    created_by: uuid.UUID
    plan: ContentPlan


async def save_draft(
    redis: Redis, *, client_id: uuid.UUID, created_by: uuid.UUID, plan: ContentPlan, ttl_s: int
) -> str:
    draft_id = uuid.uuid4().hex
    payload = {
        "client_id": str(client_id),
        "created_by": str(created_by),
        "plan": plan.model_dump(mode="json"),
    }
    await redis.set(PREFIX + draft_id, json.dumps(payload, ensure_ascii=False), ex=ttl_s)
    return draft_id


async def load_draft(redis: Redis, draft_id: str) -> Draft | None:
    if not draft_id.isalnum() or len(draft_id) > 64:
        return None
    raw = await redis.get(PREFIX + draft_id)
    if raw is None:
        return None
    data = json.loads(raw)
    return Draft(
        draft_id=draft_id,
        client_id=uuid.UUID(data["client_id"]),
        created_by=uuid.UUID(data["created_by"]),
        plan=ContentPlan.model_validate(data["plan"]),
    )
