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
