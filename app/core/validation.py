"""KMZ file validation utilities.

Provides validate_kmz() to check that uploaded files are valid KMZ archives
before storage. Size limits are enforced by the body size middleware, not here.
"""

from fastapi import UploadFile

from app.core.exceptions import InvalidKMZError

ZIP_MAGIC = b"PK\x03\x04"


async def validate_kmz(file: UploadFile) -> None:
    """Validate that an uploaded file starts with the ZIP magic bytes.

    Reads only the first 4 bytes and resets the seek pointer so the
    caller can read the entire file afterwards.

    Args:
        file: The uploaded KMZ file.

    Raises:
        InvalidKMZError: If the file does not start with the ZIP magic bytes.

    """
    header = await file.read(4)
    await file.seek(0)  # Reset so the caller can read the entire file

    if not header.startswith(ZIP_MAGIC):
        raise InvalidKMZError("File is not a valid ZIP archive")
