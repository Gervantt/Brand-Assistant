from pydantic import BaseModel, ConfigDict, Field


class ClientProfile(BaseModel):
    """Structured client facts returned by `get_client_profile` and edited by admins."""

    model_config = ConfigDict(extra="forbid")

    industry: str = ""
    description: str = ""
    audience: str = ""
    tone_of_voice: str = ""
    platforms: list[str] = Field(default_factory=list)
    content_pillars: list[str] = Field(default_factory=list)
    banned_topics: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
