class ServiceDrawingsError(Exception):
    """Base exception for all service-drawings errors."""

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "An unexpected error occurred"
        self.detail: str = self.message
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class InvalidKMZError(ServiceDrawingsError):
    """Raised when the uploaded file is not a valid ZIP/KMZ archive."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Invalid KMZ file: not a valid ZIP archive")


class KMZTooLargeError(ServiceDrawingsError):
    """Raised when the uploaded file exceeds the maximum allowed size."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "KMZ file exceeds the maximum allowed size")


class DrawingNotFoundError(ServiceDrawingsError):
    """Raised when a requested drawing does not exist in S3."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Drawing not found")


class S3Error(ServiceDrawingsError):
    """Raised when an S3 operation fails unexpectedly."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "An unexpected S3 error occurred")
