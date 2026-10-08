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


def with_cause(exc: BaseException) -> str:
    """SDK connection errors say only "Connection error."; append the underlying cause.

    Header errors embed the header value (the API key), so they are replaced by a fixed hint.
    """
    cause = exc.__cause__
    if cause is None:
        return str(exc)
    detail = str(cause)
    if "header" in detail.lower():
        detail = "illegal HTTP header value (API key contains whitespace or a newline?)"
    return f"{exc} ({type(cause).__name__}: {detail[:200]})"


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
