"""Request complexity classifier: cheap model for Q&A, strong model for generation.

The heuristic is the default — free, instant and good enough because "complex" requests in
this domain are generation tasks with very recognisable wording.
"""

import re

from brand_api.llm.types import Tier

_GENERATION_PATTERNS = [
    r"контент[- ]?план",
    r"\bплан\w*\b",
    r"сгенерир\w*|генерир\w*|генерац\w*",
    r"\b(напиши|написать|напишите|составь|составить|составьте|придумай|придумать|подготовь)\w*",
    r"\bпост\w*\b",
    r"\bсторис\b|\bstories\b",
    r"\bбриф\w*",
    r"\bрубрик\w*",
    r"на\s+(неделю|месяц|квартал)",
    r"\b(content plan|write|draft|generate|brief)\b",
]
_GENERATION_RE = re.compile("|".join(_GENERATION_PATTERNS), re.IGNORECASE)

LONG_REQUEST_CHARS = 600
MANY_QUESTIONS = 3


def classify_heuristic(text: str) -> Tier:
    if _GENERATION_RE.search(text):
        return Tier.COMPLEX
    if len(text) > LONG_REQUEST_CHARS or text.count("?") >= MANY_QUESTIONS:
        return Tier.COMPLEX
    return Tier.SIMPLE


CLASSIFIER_PROMPT = (
    "Classify the user's request for a brand assistant. Answer with exactly one word.\n"
    "simple — a factual question about the brand book (tone, colours, audience, rules).\n"
    "complex — generating content (plans, posts, stories, briefs) or multi-part analysis."
)


def parse_classifier_answer(answer: str) -> Tier | None:
    word = answer.strip().lower()
    if word.startswith("complex"):
        return Tier.COMPLEX
    if word.startswith("simple"):
        return Tier.SIMPLE
    return None
