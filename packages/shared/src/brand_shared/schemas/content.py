"""Structured generation outputs. Strictly validated; the model gets one repair attempt."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

ContentFormat = Literal["post", "carousel", "reels", "stories", "video", "article"]


def _hashtag(value: str) -> str:
    value = value.strip().replace(" ", "")
    if not value:
        raise ValueError("empty hashtag")
    return value if value.startswith("#") else f"#{value}"


Hashtag = Annotated[str, AfterValidator(_hashtag)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentPlanItem(_Strict):
    date: date
    platform: str = Field(min_length=2, max_length=40, description="Instagram, Telegram, TikTok…")
    format: ContentFormat
    rubric: str = Field(min_length=2, max_length=80, description="Рубрика из контент-пиллар бренда")
    title: str = Field(min_length=3, max_length=120)
    text: str = Field(min_length=20, max_length=1200, description="Черновик текста публикации")
    hashtags: list[Hashtag] = Field(default_factory=list, max_length=12)
    visual_idea: str = Field(min_length=5, max_length=400, description="Идея визуала")


class ContentPlan(_Strict):
    title: str = Field(min_length=3, max_length=160)
    period_start: date
    period_end: date
    goal: str = Field(min_length=3, max_length=400)
    items: list[ContentPlanItem] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _dates_inside_period(self) -> Self:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        outside = [
            i.date.isoformat()
            for i in self.items
            if not self.period_start <= i.date <= self.period_end
        ]
        if outside:
            raise ValueError(f"item dates outside the period: {', '.join(outside)}")
        return self


class Post(_Strict):
    platform: str = Field(min_length=2, max_length=40)
    format: ContentFormat
    title: str = Field(min_length=3, max_length=120)
    text: str = Field(min_length=20, max_length=2200)
    hashtags: list[Hashtag] = Field(default_factory=list, max_length=15)
    call_to_action: str = Field(default="", max_length=200)
    visual_idea: str = Field(min_length=5, max_length=400)


class DesignerBrief(_Strict):
    title: str = Field(min_length=3, max_length=120)
    platform: str = Field(min_length=2, max_length=40)
    format: ContentFormat
    dimensions: str = Field(min_length=3, max_length=40, description="Например 1080x1350")
    objective: str = Field(min_length=5, max_length=400)
    key_message: str = Field(min_length=3, max_length=300)
    text_on_visual: str = Field(default="", max_length=200)
    visual_description: str = Field(min_length=10, max_length=1200)
    mood: list[str] = Field(default_factory=list, max_length=8)
    colors: list[str] = Field(default_factory=list, max_length=8)
    do: list[str] = Field(default_factory=list, max_length=10)
    dont: list[str] = Field(default_factory=list, max_length=10)
    deliverables: list[str] = Field(min_length=1, max_length=10)
