"""Async S3 client wrapper for KMZ drawing storage."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Annotated

import botocore.exceptions
from types_aiobotocore_s3 import S3Client

from fastapi import Depends, Request

from app.core.exceptions import DrawingNotFoundError, S3Error
from app.settings import SettingsDep

logger = logging.getLogger(__name__)

_HTTP_NOT_FOUND = 404


class S3Service:
    """Async S3 client wrapper for KMZ drawing storage."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def upload_kml(self, key: str, data: bytes, content_type: str, sha256: str) -> None:
        """Upload a KMZ file to S3 with SHA-256 metadata.

        Args:
            key: S3 object key (e.g. "drawings/{uuid}.kmz")
            data: KMZ file bytes
            content_type: MIME type (application/vnd.google-earth.kmz)
            sha256: Hex digest of the content

        Raises:
            S3Error: If the S3 upload fails

        """
        try:
            await self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata={"sha256": sha256},
            )
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 upload failed for key %s", key)
            raise S3Error(f"S3 upload failed for key {key}: {e}") from e

    async def get_kml(self, key: str) -> AsyncIterator[bytes]:
        """Stream a KMZ file from S3.

        Args:
            key: S3 object key

        Yields:
            Chunks of the KMZ file bytes

        Raises:
            DrawingNotFoundError: If the object does not exist
            S3Error: If the S3 read fails

        """
        try:
            response = await self._client.get_object(Bucket=self._bucket, Key=key)
            async for chunk in response["Body"]:
                yield chunk
        except botocore.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            status_code = e.response["ResponseMetadata"]["HTTPStatusCode"]
            if error_code == "NoSuchKey" or status_code == _HTTP_NOT_FOUND:
                raise DrawingNotFoundError(f"Drawing not found: {key}") from e
            logger.exception("S3 read failed for key %s", key)
            raise S3Error(f"S3 read failed for key {key}: {e}") from e
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 read failed for key %s", key)
            raise S3Error(f"S3 read failed for key {key}: {e}") from e

    async def head_kml(self, key: str) -> dict[str, str]:
        """Get S3 object metadata without downloading the body.

        Args:
            key: S3 object key

        Returns:
            Dict of object metadata

        Raises:
            DrawingNotFoundError: If the object does not exist
            S3Error: If the S3 head request fails

        """
        try:
            response = await self._client.head_object(Bucket=self._bucket, Key=key)
            return response.get("Metadata", {})
        except botocore.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            status_code = e.response["ResponseMetadata"]["HTTPStatusCode"]
            if error_code == "NoSuchKey" or status_code == _HTTP_NOT_FOUND:
                raise DrawingNotFoundError(f"Drawing not found: {key}") from e
            logger.exception("S3 head failed for key %s", key)
            raise S3Error(f"S3 head failed for key {key}: {e}") from e
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 head failed for key %s", key)
            raise S3Error(f"S3 head failed for key {key}: {e}") from e

    async def check_bucket(self) -> bool:
        """Check whether the configured S3 bucket is accessible.

        Performs a head_bucket request to verify S3 connectivity.
        Used by the Kubernetes readiness probe.

        Returns:
            True if the bucket is reachable, False otherwise.

        """
        try:
            await self._client.head_bucket(Bucket=self._bucket)
        except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError):
            logger.exception("S3 bucket connectivity check failed for bucket %s", self._bucket)
            return False
        else:
            return True

async def get_s3_client(
    request: Request, settings: SettingsDep
) -> AsyncGenerator[S3Client]:
    """FastAPI dependency that provides a per-request S3 client from the shared session.

    The aioboto3 session is created once in the application lifespan and stored
    on app.state.s3_session. This dependency creates a short-lived S3 client
    from that shared session for the duration of a single request.
    """
    session = request.app.state.s3_session
    async with session.client(
        "s3",
        endpoint_url=settings.aws_s3_endpoint_url,
    ) as s3_client:
        yield s3_client

S3ClientDep = Annotated[S3Client, Depends(get_s3_client)]

async def get_s3_service(
    client: S3ClientDep,
    settings: SettingsDep,
) -> S3Service:
    """FastAPI dependency that provides a configured S3Service instance."""
    return S3Service(client=client, bucket=settings.aws_s3_bucket_name)

S3ServiceDep = Annotated[S3Service, Depends(get_s3_service)]
