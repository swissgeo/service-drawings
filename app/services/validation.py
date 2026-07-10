from app.exceptions import InvalidKMZError, KMZTooLargeError

MAX_KMZ_SIZE = 5 * 1024 * 1024  # 5 MB default
ZIP_MAGIC = b"PK\x03\x04"


def validate_kmz(content: bytes, max_size: int = MAX_KMZ_SIZE) -> None:
    """Validate KMZ file content.

    Args:
        content: Raw file bytes
        max_size: Maximum allowed size in bytes (default: 5 MB)

    Raises:
        KMZTooLargeError: If content exceeds max_size
        InvalidKMZError: If content does not start with ZIP magic bytes
    """
    if len(content) > max_size:
        raise KMZTooLargeError(
            f"File size {len(content)} bytes exceeds maximum {max_size} bytes"
        )
    if not content.startswith(ZIP_MAGIC):
        raise InvalidKMZError("File is not a valid ZIP archive")
