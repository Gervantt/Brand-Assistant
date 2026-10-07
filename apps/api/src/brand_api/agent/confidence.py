"""Model self-assessment: the model appends `<confidence>0.8</confidence>` to brand-book answers.
The marker is stripped from the token stream (never shown to the user) and combined with the
retrieval score into one confidence value."""

import re
from dataclasses import dataclass

_TAG = re.compile(r"<confidence>\s*([0-9]*[.,]?[0-9]+)\s*(?:</confidence>|$)", re.IGNORECASE)
_OPEN = "<confidence>"


def _parse(raw: str) -> float:
    return min(max(float(raw.replace(",", ".")), 0.0), 1.0)


def strip_confidence(text: str) -> tuple[str, float | None]:
    match = _TAG.search(text)
    if match is None:
        return text, None
    return (text[: match.start()] + text[match.end() :]).rstrip(), _parse(match.group(1))


class ConfidenceFilter:
    """Streaming version: holds back anything that might be the start of the marker."""

    def __init__(self) -> None:
        self._buffer = ""
        self.value: float | None = None

    def feed(self, text: str) -> str:
        self._buffer += text
        self._extract(final=False)
        cut = self._hold_from()
        out, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return out

    def flush(self) -> str:
        self._extract(final=True)
        out, self._buffer = self._buffer, ""
        return out.rstrip() if self.value is not None else out

    def _extract(self, *, final: bool) -> None:
        match = _TAG.search(self._buffer)
        if match is None:
            return
        closed = match.group(0).lower().endswith("</confidence>")
        if closed or final:
            self.value = _parse(match.group(1))
            self._buffer = self._buffer[: match.start()] + self._buffer[match.end() :]

    def _hold_from(self) -> int:
        """Index from which the buffer might still become a marker."""
        lowered = self._buffer.lower()
        opened = lowered.rfind(_OPEN)
        if opened != -1:  # an open, not yet closed marker
            return opened
        for size in range(min(len(_OPEN) - 1, len(lowered)), 0, -1):
            if lowered.endswith(_OPEN[:size]):  # "<", "<conf", ... at the very end
                return len(lowered) - size
        return len(lowered)


@dataclass(frozen=True)
class Confidence:
    retrieval: float
    self_assessed: float | None
    combined: float
    low: bool


def combine(
    retrieval: float, self_assessed: float | None, *, weight: float, threshold: float
) -> Confidence:
    combined = (
        retrieval if self_assessed is None else weight * retrieval + (1 - weight) * self_assessed
    )
    return Confidence(
        retrieval=round(retrieval, 3),
        self_assessed=self_assessed,
        combined=round(combined, 3),
        low=combined < threshold,
    )
