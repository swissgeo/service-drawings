import aioboto3

import pytest

from app.core.exceptions import DrawingNotFoundError
from app.core.s3 import S3Service


@pytest.mark.asyncio
async def test_upload_kml_success(settings, s3_client) -> None:
    """Upload a KMZ and verify it exists via the sync S3 client."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_s3_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/test-uuid.kmz"
        data = b"fake kmz content"
        sha256 = "abc123"

        await svc.upload_kml(key, data, "application/vnd.google-earth.kmz", sha256)

        response = s3_client.head_object(Bucket=settings.aws_s3_bucket_name, Key=key)
        assert response["ContentLength"] == len(data)
        assert response["Metadata"]["sha256"] == sha256


@pytest.mark.asyncio
async def test_get_kml_success(settings) -> None:
    """Upload then stream back, verify content matches."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_s3_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/get-test.kmz"
        data = b"content to stream back"
        sha256 = "def456"

        await svc.upload_kml(key, data, "application/vnd.google-earth.kmz", sha256)

        chunks = [chunk async for chunk in svc.get_kml(key)]
        assert b"".join(chunks) == data


@pytest.mark.asyncio
async def test_get_kml_not_found(settings) -> None:
    """Get a non-existent key should raise DrawingNotFoundError."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_s3_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/nonexistent.kmz"

        with pytest.raises(DrawingNotFoundError):
            async for _ in svc.get_kml(key):
                pass


@pytest.mark.asyncio
async def test_head_kml_success(settings) -> None:
    """Upload with metadata, then head and verify the metadata."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_s3_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/head-test.kmz"
        data = b"head test content"
        sha256 = "ghi789"

        await svc.upload_kml(key, data, "application/vnd.google-earth.kmz", sha256)

        metadata = await svc.head_kml(key)
        assert metadata == {"sha256": sha256}


@pytest.mark.asyncio
async def test_head_kml_not_found(settings) -> None:
    """Head a non-existent key should raise DrawingNotFoundError."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_s3_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        key = "drawings/nonexistent-head.kmz"

        with pytest.raises(DrawingNotFoundError):
            await svc.head_kml(key)


@pytest.mark.asyncio
async def test_check_bucket_success(settings) -> None:
    """check_bucket returns True when the S3 bucket is reachable."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=settings.aws_s3_endpoint_url) as client:  # type: ignore  # noqa: PGH003
        svc = S3Service(client=client, bucket=settings.aws_s3_bucket_name)
        result = await svc.check_bucket()
        assert result is True
