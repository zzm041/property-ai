"""Analysis domain errors. Everything else uses the families in app.core.errors."""


class ObservationBindingError(ValueError):
    """Machine-readable guard failure, without source text in the exception."""

    def __init__(self, message: str, *, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code
