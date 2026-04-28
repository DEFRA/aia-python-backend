"""Custom exceptions for the Defra pipeline."""


class ScannedPdfError(RuntimeError):
    """Raised when a PDF has no extractable text layer."""


class UnknownCategoryError(Exception):
    """Raised when no assessment input matches the requested category."""
