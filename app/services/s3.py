"""Async S3 client wrapper for KMZ drawing storage."""

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated

import aioboto3
import botocore.exceptions

from fastapi import Depends

from app.exceptions import DrawingNotFoundError, S3Error
from app.settings import get_settings

if TYPE_CHECKING:
    from app.settings import Settings

logger = logging.getLogger(__name__)

_HTTP_NOT_FOUND = 404

_session: aioboto3.Session | None = None


def _get_session() -> aioboto3.Session:
    """Return the module-level aioboto3 session singleton."""
    global _session  # noqa: PLW0603
    if _session is None:
        _session = aioboto3.Session()
    return _session


class S3Service:
    """Async S3 client wrapper for KMZ drawing storage."""

    def __init__(self, bucket: str, endpoint_url: str | None = None) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url

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
            async with _get_session().client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                await client.put_object(
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
            async with _get_session().client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                response = await client.get_object(Bucket=self._bucket, Key=key)
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
            async with _get_session().client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                response = await client.head_object(Bucket=self._bucket, Key=key)
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
            async with _get_session().client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                await client.head_bucket(Bucket=self._bucket)
        except botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError:
            logger.exception("S3 bucket connectivity check failed for bucket %s", self._bucket)
            return False
        else:
            return True


def get_s3_service(
    settings: "Settings" = Depends(get_settings),  # noqa: B008, UP037
) -> "S3Service":  # noqa: UP037
    """FastAPI dependency that provides a configured S3Service instance.

    Args:
        settings: Application settings injected via FastAPI dependency.

    Returns:
        A new S3Service configured with the application's bucket and endpoint.

    """
    return S3Service(
        bucket=settings.aws_s3_bucket_name,
        endpoint_url=settings.aws_s3_endpoint_url,
    )


S3ServiceDep = Annotated[
    S3Service,
    Depends(get_s3_service),
]
