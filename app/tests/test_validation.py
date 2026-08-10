import io
import zipfile

from fastapi import UploadFile

import pytest

from app.core.exceptions import InvalidKMZError
from app.core.validation import ZIP_MAGIC, validate_kmz


def make_upload(data: bytes) -> UploadFile:
    """Create an UploadFile wrapping the given bytes."""
    return UploadFile(file=io.BytesIO(data))


def make_minimal_zip() -> bytes:
    """Create a minimal valid ZIP file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test.kml", "x")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_valid_zip_passes() -> None:
    """A valid minimal ZIP file should pass validation."""
    await validate_kmz(make_upload(make_minimal_zip()))


@pytest.mark.asyncio
async def test_non_zip_fails_with_invalid_kmz() -> None:
    """Random bytes should raise InvalidKMZError."""
    with pytest.raises(InvalidKMZError):
        await validate_kmz(make_upload(b"not a zip"))


@pytest.mark.asyncio
async def test_empty_body_fails_with_invalid_kmz() -> None:
    """Empty content should raise InvalidKMZError (no ZIP magic)."""
    with pytest.raises(InvalidKMZError):
        await validate_kmz(make_upload(b""))


@pytest.mark.asyncio
async def test_seek_pointer_reset() -> None:
    """After validation the seek pointer should be reset to 0."""
    upload = make_upload(make_minimal_zip())
    await validate_kmz(upload)
    assert await upload.read(4) == ZIP_MAGIC
