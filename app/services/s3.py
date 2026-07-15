from collections.abc import AsyncIterator

import aioboto3
import botocore.exceptions

from app.exceptions import DrawingNotFoundError, S3Error

_HTTP_NOT_FOUND = 404


class S3Service:
    """Async S3 client wrapper for KMZ drawing storage."""

    def __init__(self, bucket: str, endpoint_url: str | None = None) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._session = aioboto3.Session()

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
            async with self._session.client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                await client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                    Metadata={"sha256": sha256},
                )
        except Exception as e:
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
            async with self._session.client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                response = await client.get_object(
                    Bucket=self._bucket, Key=key
                )
                async for chunk in response["Body"]:
                    yield chunk
        except botocore.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            status_code = e.response["ResponseMetadata"]["HTTPStatusCode"]
            if error_code == "NoSuchKey" or status_code == _HTTP_NOT_FOUND:
                raise DrawingNotFoundError(f"Drawing not found: {key}") from e
            raise S3Error(f"S3 read failed for key {key}: {e}") from e
        except Exception as e:
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
            async with self._session.client(  # type: ignore  # noqa: PGH003
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                response = await client.head_object(
                    Bucket=self._bucket, Key=key
                )
                return response.get("Metadata", {})
        except botocore.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            status_code = e.response["ResponseMetadata"]["HTTPStatusCode"]
            if error_code == "NoSuchKey" or status_code == _HTTP_NOT_FOUND:
                raise DrawingNotFoundError(f"Drawing not found: {key}") from e
            raise S3Error(f"S3 head failed for key {key}: {e}") from e
        except Exception as e:
            raise S3Error(f"S3 head failed for key {key}: {e}") from e
