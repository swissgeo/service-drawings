import io
import zipfile

import pytest

from app.exceptions import InvalidKMZError, KMZTooLargeError
from app.services.validation import MAX_KMZ_SIZE, ZIP_MAGIC, validate_kmz


def make_minimal_zip() -> bytes:
    """Create a minimal valid ZIP file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test.kml", "x")
    return buf.getvalue()


def make_padded_zip_bytes(size: int) -> bytes:
    """Create bytes starting with ZIP magic padded to exactly *size* bytes."""
    return ZIP_MAGIC + b"\x00" * (size - len(ZIP_MAGIC))


class TestValidateKMZ:
    """Tests for the validate_kmz function."""

    def test_valid_zip_passes(self) -> None:
        """A valid minimal ZIP file should pass validation."""
        content = make_minimal_zip()
        validate_kmz(content)

    def test_non_zip_fails_with_invalid_kmz(self) -> None:
        """Random bytes should raise InvalidKMZError."""
        with pytest.raises(InvalidKMZError):
            validate_kmz(b"not a zip")

    def test_exactly_5mb_passes(self) -> None:
        """Content exactly at the max size limit should pass."""
        content = make_padded_zip_bytes(MAX_KMZ_SIZE)
        assert len(content) == MAX_KMZ_SIZE
        validate_kmz(content)

    def test_5mb_plus_1_fails_with_too_large(self) -> None:
        """Content that exceeds max size should raise KMZTooLargeError."""
        content = make_padded_zip_bytes(MAX_KMZ_SIZE + 1)
        with pytest.raises(KMZTooLargeError):
            validate_kmz(content)

    def test_empty_body_fails_with_invalid_kmz(self) -> None:
        """Empty content should raise InvalidKMZError (no ZIP magic)."""
        with pytest.raises(InvalidKMZError):
            validate_kmz(b"")
