import io
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aioboto3
import botocore.exceptions

import pytest

from app.core.exceptions import DrawingNotFoundError, S3Error
from app.core.s3 import S3Service


@pytest.mark.asyncio
async def test_upload_drawing_success(settings, s3_client) -> None:
    """Upload a KMZ and verify it exists via the sync S3 client."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/test-uuid.kmz"
        data = b"fake kmz content"
        metadata = {
            "sha256": "abc123",
            "admin-id": "11111111-1111-1111-1111-111111111111",
            "created-at": "2026-01-01T00:00:00+00:00",
            "modified-at": "2026-01-01T00:00:00+00:00",
        }

        await svc.upload_drawing(
            key, io.BytesIO(data), "application/vnd.google-earth.kmz", metadata
        )

        response = s3_client.head_object(Bucket=settings.aws_s3_bucket_name, Key=key)
        assert response["ContentLength"] == len(data)
        assert response["Metadata"] == metadata
        assert response["CacheControl"] == "no-store, max-age=0"


@pytest.mark.asyncio
async def test_get_drawing_success(settings) -> None:
    """Upload then stream back, verify content matches."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/get-test.kmz"
        data = b"content to stream back"
        metadata = {
            "sha256": "def456",
            "admin-id": "11111111-1111-1111-1111-111111111111",
            "created-at": "2026-01-01T00:00:00+00:00",
            "modified-at": "2026-01-01T00:00:00+00:00",
        }

        await svc.upload_drawing(
            key, io.BytesIO(data), "application/vnd.google-earth.kmz", metadata
        )

        chunks = [chunk async for chunk in svc.get_drawing(key)]
        assert b"".join(chunks) == data


@pytest.mark.asyncio
async def test_get_drawing_not_found(settings) -> None:
    """Get a non-existent key should raise DrawingNotFoundError."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/nonexistent.kmz"

        with pytest.raises(DrawingNotFoundError):
            async for _ in svc.get_drawing(key):
                pass


@pytest.mark.asyncio
async def test_head_drawing_success(settings) -> None:
    """Upload with metadata, then head and verify the metadata."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/head-test.kmz"
        data = b"head test content"
        expected_metadata = {
            "sha256": "ghi789",
            "admin-id": "11111111-1111-1111-1111-111111111111",
            "created-at": "2026-01-01T00:00:00+00:00",
            "modified-at": "2026-01-01T00:00:00+00:00",
        }

        await svc.upload_drawing(
            key, io.BytesIO(data), "application/vnd.google-earth.kmz", expected_metadata
        )

        metadata = await svc.head_drawing(key)
        assert metadata == expected_metadata


@pytest.mark.asyncio
async def test_head_drawing_not_found(settings) -> None:
    """Head a non-existent key should raise DrawingNotFoundError."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/nonexistent-head.kmz"

        with pytest.raises(DrawingNotFoundError):
            await svc.head_drawing(key)


@pytest.mark.asyncio
async def test_check_bucket_success(settings) -> None:
    """check_bucket returns True when the S3 bucket is reachable."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        result = await svc.check_bucket()
        assert result is True


# ---------------------------------------------------------------------------
# Error-path tests using a mocked client (no moto needed)
# ---------------------------------------------------------------------------


def _client_error(code: str, status: int, operation: str) -> botocore.exceptions.ClientError:
    """Build a botocore ClientError with the given code and HTTP status."""
    return botocore.exceptions.ClientError(
        cast(
            "Any",
            {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        ),
        operation,
    )


@pytest.mark.asyncio
async def test_upload_drawing_botocore_error(settings) -> None:
    """A BotoCoreError during upload should raise S3Error."""
    client = MagicMock()
    client.upload_fileobj = AsyncMock(side_effect=botocore.exceptions.BotoCoreError())
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        await svc.upload_drawing(
            "drawings/x.kmz",
            io.BytesIO(b"data"),
            "application/vnd.google-earth.kmz",
            {"sha256": "abc"},
        )


@pytest.mark.asyncio
async def test_get_drawing_client_error(settings) -> None:
    """A non-NoSuchKey ClientError during read should raise S3Error."""
    client = MagicMock()
    client.get_object = AsyncMock(side_effect=_client_error("AccessDenied", 403, "GetObject"))
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        async for _ in svc.get_drawing("drawings/x.kmz"):
            pass


@pytest.mark.asyncio
async def test_get_drawing_botocore_error(settings) -> None:
    """A BotoCoreError during read should raise S3Error."""
    client = MagicMock()
    client.get_object = AsyncMock(side_effect=botocore.exceptions.BotoCoreError())
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        async for _ in svc.get_drawing("drawings/x.kmz"):
            pass


@pytest.mark.asyncio
async def test_head_drawing_missing_metadata(settings) -> None:
    """Head returning no metadata should raise S3Error."""
    client = MagicMock()
    client.head_object = AsyncMock(return_value={"Metadata": None})
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        await svc.head_drawing("drawings/x.kmz")


@pytest.mark.asyncio
async def test_head_drawing_client_error(settings) -> None:
    """A non-NoSuchKey ClientError during head should raise S3Error."""
    client = MagicMock()
    client.head_object = AsyncMock(side_effect=_client_error("AccessDenied", 403, "HeadObject"))
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        await svc.head_drawing("drawings/x.kmz")


@pytest.mark.asyncio
async def test_head_drawing_botocore_error(settings) -> None:
    """A BotoCoreError during head should raise S3Error."""
    client = MagicMock()
    client.head_object = AsyncMock(side_effect=botocore.exceptions.BotoCoreError())
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        await svc.head_drawing("drawings/x.kmz")


@pytest.mark.asyncio
async def test_check_bucket_failure(settings) -> None:
    """check_bucket returns False when the bucket is unreachable."""
    client = MagicMock()
    client.head_bucket = AsyncMock(side_effect=_client_error("404", 404, "HeadBucket"))
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    assert await svc.check_bucket() is False


@pytest.mark.asyncio
async def test_delete_drawing_success(settings) -> None:
    """Upload then delete, verify the object is gone."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/delete-test.kmz"
        data = b"content to delete"
        metadata = {
            "sha256": "jkl012",
            "admin-id": "11111111-1111-1111-1111-111111111111",
            "created-at": "2026-01-01T00:00:00+00:00",
            "modified-at": "2026-01-01T00:00:00+00:00",
        }

        await svc.upload_drawing(
            key, io.BytesIO(data), "application/vnd.google-earth.kmz", metadata
        )

        await svc.delete_drawing(key)

        with pytest.raises(DrawingNotFoundError):
            await svc.head_drawing(key)


@pytest.mark.asyncio
async def test_delete_drawing_botocore_error(settings) -> None:
    """A BotoCoreError during delete should raise S3Error."""
    client = MagicMock()
    client.delete_object = AsyncMock(side_effect=botocore.exceptions.BotoCoreError())
    svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)

    with pytest.raises(S3Error):
        await svc.delete_drawing("drawings/x.kmz")
