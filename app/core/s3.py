"""Async S3 client wrapper for KMZ drawing storage."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from http import HTTPStatus
from typing import Annotated, BinaryIO

import botocore.exceptions
from types_aiobotocore_s3 import S3Client

from fastapi import Depends, Request

from app.core.exceptions import DrawingNotFoundError, S3Error
from app.settings import SettingsDep

logger = logging.getLogger(__name__)

CACHE_CONTROL_NO_STORE = "no-store, max-age=0"


class S3Service:
    """Async S3 client wrapper for KMZ drawing storage."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def upload_drawing(
        self, key: str, fileobj: BinaryIO, content_type: str, metadata: dict[str, str]
    ) -> None:
        """Upload a KMZ file to S3 with the given metadata.

        Streams the file object without loading it into memory.

        Args:
            key: S3 object key (e.g. "drawings/{uuid}.kmz")
            fileobj: Open seekable binary file object to upload
            content_type: MIME type (application/vnd.google-earth.kmz)
            metadata: Dict of S3 metadata (sha256, admin_id, created_at,
                modified_at)

        Raises:
            S3Error: If the S3 upload fails

        """
        try:
            await self._client.upload_fileobj(
                Fileobj=fileobj,
                Bucket=self._bucket,
                Key=key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": CACHE_CONTROL_NO_STORE,
                    "Metadata": metadata,
                },
            )
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 upload failed for key %s", key)
            raise S3Error(f"S3 upload failed for key {key}: {e}") from e

    async def get_drawing(self, key: str) -> AsyncIterator[bytes]:
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
            if error_code == "NoSuchKey" or status_code == HTTPStatus.NOT_FOUND:
                raise DrawingNotFoundError(f"Drawing not found: {key}") from e
            logger.exception("S3 read failed for key %s", key)
            raise S3Error(f"S3 read failed for key {key}: {e}") from e
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 read failed for key %s", key)
            raise S3Error(f"S3 read failed for key {key}: {e}") from e

    async def head_drawing(self, key: str) -> dict[str, str]:
        """Get S3 object metadata without downloading the body.

        Args:
            key: S3 object key

        Returns:
            Dict of object metadata

        Raises:
            DrawingNotFoundError: If the object does not exist
            S3Error: If the S3 head request fails or metadata is missing

        """
        try:
            response = await self._client.head_object(Bucket=self._bucket, Key=key)
            metadata = response.get("Metadata")
            if not metadata:
                logger.error("Metadata missing for S3 object %s", key)
                raise S3Error(f"Metadata missing for S3 object {key}")
        except botocore.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            status_code = e.response["ResponseMetadata"]["HTTPStatusCode"]
            if error_code == "NoSuchKey" or status_code == HTTPStatus.NOT_FOUND:
                raise DrawingNotFoundError(f"Drawing not found: {key}") from e
            logger.exception("S3 head failed for key %s", key)
            raise S3Error(f"S3 head failed for key {key}: {e}") from e
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 head failed for key %s", key)
            raise S3Error(f"S3 head failed for key {key}: {e}") from e
        else:
            return metadata

    async def delete_drawing(self, key: str) -> None:
        """Delete a KMZ file from S3.

        Args:
            key: S3 object key

        Raises:
            S3Error: If the S3 delete fails

        """
        try:
            await self._client.delete_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.BotoCoreError as e:
            logger.exception("S3 delete failed for key %s", key)
            raise S3Error(f"S3 delete failed for key {key}: {e}") from e

    async def check_bucket(self) -> bool:
        """Check whether the configured S3 bucket is accessible.

        Performs a head_bucket request to verify S3 connectivity.

        Returns:
            True if the bucket is reachable, False otherwise.

        """
        try:
            await self._client.head_bucket(Bucket=self._bucket)
        except botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError:
            logger.exception("S3 bucket connectivity check failed for bucket %s", self._bucket)
            return False
        else:
            return True


async def get_s3_client(request: Request, settings: SettingsDep) -> AsyncGenerator[S3Client]:
    """FastAPI dependency that provides a per-request S3 client from the shared session.

    The aioboto3 session is created once in the application lifespan and stored
    on app.state.s3_session. This dependency creates a short-lived S3 client
    from that shared session for the duration of a single request.
    """
    session = request.app.state.s3_session
    async with session.client(
        "s3",
        endpoint_url=settings.aws_endpoint_url,
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
