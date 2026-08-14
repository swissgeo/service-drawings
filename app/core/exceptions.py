"""Custom exception hierarchy for service-drawings.

Defines a base ServiceDrawingsError and three subclasses for specific failure
modes: invalid KMZ content, missing drawings, and S3 errors. Size limits are
enforced by the body size middleware, so there is no oversized-file exception.
"""


class ServiceDrawingsError(Exception):
    """Base exception for all service-drawings errors."""

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "An unexpected error occurred"
        self.detail: str = self.message
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return the exception message."""
        return self.message


class InvalidKMZError(ServiceDrawingsError):
    """Raised when the uploaded file is not a valid ZIP/KMZ archive."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Invalid KMZ file: not a valid ZIP archive")


class DigestMismatchError(ServiceDrawingsError):
    """Raised when the client-provided SHA-256 does not match the uploaded content."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "SHA-256 digest does not match the uploaded content")


class DrawingNotFoundError(ServiceDrawingsError):
    """Raised when a requested drawing does not exist in S3."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Drawing not found")


class S3Error(ServiceDrawingsError):
    """Raised when an S3 operation fails unexpectedly."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "An unexpected S3 error occurred")
