"""Optional provider-reported token counts, never guessed from text or duration."""
from collections.abc import Mapping


def valid_token_count(value: object) -> int | None:
    # AiCallLog uses a signed SQL INTEGER; bool is not a measurement.
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def response_token_usage(payload: object) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    total = valid_token_count(usage.get("total_tokens"))
    if total is not None:
        return total
    prompt = valid_token_count(usage.get("prompt_tokens"))
    completion = valid_token_count(usage.get("completion_tokens"))
    return valid_token_count(prompt + completion) if prompt is not None and completion is not None else None


class StructuredCompletion(dict[str, object]):
    """Keep the Mapping contract; usage is metadata, not an extra output field."""

    def __init__(self, values: Mapping[str, object], *, token_usage: int | None) -> None:
        super().__init__(values)
        self.token_usage = valid_token_count(token_usage)
