"""External provider failures. Never include secrets in messages."""


class ProviderError(Exception):
    error_code = "AI.PROVIDER_FAILED"


class ProviderTimeoutError(ProviderError):
    error_code = "AI.TIMEOUT"


class ProviderUnavailableError(ProviderError):
    error_code = "AI.UNAVAILABLE"


class ProviderInvalidOutputError(ProviderError):
    error_code = "AI.INVALID_OUTPUT"


class ProviderRateLimitError(ProviderError):
    error_code = "AI.RATE_LIMITED"


class ProviderOutputUnverifiableError(ProviderError):
    """The provider ran but produced no single usable answer (PA-169/F2)."""

    error_code = "AI.OUTPUT_UNVERIFIABLE"


class ProviderInputQuarantinedError(ProviderError):
    """PA-169: the adapter refused this exact input, not the budget.

    Raised only when the adapter's 503 says ``INPUT_QUARANTINED``: the model
    once answered this same text badly, so re-sending it needs an operator's
    explicit retry. Reporting it as unavailable or rate limited hid that path.
    """

    error_code = "AI.INPUT_QUARANTINED"
