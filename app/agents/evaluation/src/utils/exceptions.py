"""Custom exceptions for the Defra pipeline."""


class ScannedPdfError(RuntimeError):
    """Raised when a PDF has no extractable text layer."""
