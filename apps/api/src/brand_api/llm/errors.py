from dataclasses import dataclass


class LLMError(Exception):
    """A single provider call failed. `retryable` drives retry vs. immediate fallback."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        retryable: bool,
        retry_after: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.retry_after = retry_after
        self.status_code = status_code


@dataclass(frozen=True)
class FailedAttempt:
    model: str
    kind: str
    message: str


class AllModelsFailedError(Exception):
    """Every model in the fallback chain failed. Safe to show a generic message to users."""

    user_message = "Сервис ИИ временно недоступен. Попробуйте ещё раз через минуту."

    def __init__(self, attempts: list[FailedAttempt]) -> None:
        summary = "; ".join(f"{a.model}: {a.kind}" for a in attempts) or "no models configured"
        super().__init__(f"all models failed ({summary})")
        self.attempts = attempts


class NoProviderConfiguredError(AllModelsFailedError):
    user_message = "Не настроен ни один LLM-провайдер. Укажите GROQ_API_KEY в .env."

    def __init__(self) -> None:
        super().__init__([])


class StreamInterruptedError(Exception):
    """The stream failed after tokens were already sent; fallback is no longer possible."""

    user_message = "Ответ прервался. Попробуйте повторить запрос."


class StructuredOutputError(Exception):
    """Model output did not validate against the schema even after one repair attempt."""

    user_message = "Модель вернула некорректный результат. Попробуйте переформулировать запрос."

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw
