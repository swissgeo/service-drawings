"""Drawings business logic — validation, S3 storage, URL construction."""

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, UploadFile
from pydantic import HttpUrl

from app.core.s3 import S3Service, S3ServiceDep
from app.core.validation import validate_kmz
from app.schemas.drawings import DrawingsCreateResponse
from app.settings import Settings, SettingsDep

logger = logging.getLogger(__name__)


class DrawingsService:
    """Business logic for KMZ drawing creation and retrieval."""

    S3_KEY_PREFIX = "drawings"
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"

    def __init__(self, s3: S3Service, settings: Settings) -> None:
        self._s3 = s3
        self._settings = settings

    @staticmethod
    def build_s3_key(drawing_id: uuid.UUID) -> str:
        """Build the S3 object key for a drawing identifier.

        Args:
            drawing_id: The UUID of the drawing.

        Returns:
            The S3 key in the format "drawings/{uuid}.kmz".

        """
        return f"{DrawingsService.S3_KEY_PREFIX}/{drawing_id}.kmz"

    @staticmethod
    def build_cloudfront_url(domain: str, drawing_id: uuid.UUID) -> str:
        """Build the CloudFront URL for accessing a drawing.

        Args:
            domain: The CloudFront domain hostname (without https:// prefix).
            drawing_id: The UUID of the drawing.

        Returns:
            The full HTTPS URL to the KMZ file on CloudFront.

        """
        return f"https://{domain}/{DrawingsService.S3_KEY_PREFIX}/{drawing_id}.kmz"

    async def create_drawing(self, file: UploadFile) -> DrawingsCreateResponse:
        """Validate, hash, upload a KMZ drawing and return its metadata.

        Args:
            file: The KMZ file uploaded as multipart/form-data.

        Returns:
            A response containing the drawing ID, admin ID placeholder,
            and the access URL where the file can be retrieved.

        Raises:
            InvalidKMZError: If the uploaded file is not a valid ZIP archive.
            S3Error: If the S3 upload operation fails.

        """
        await validate_kmz(file)

        content_hash = hashlib.sha256()
        size = 0
        while chunk := await file.read(1024 * 1024):
            content_hash.update(chunk)
            size += len(chunk)
        await file.seek(0)  # reset so upload_fileobj reads from the start

        drawing_id = uuid.uuid4()
        admin_id = uuid.uuid4()

        s3_key = self.build_s3_key(drawing_id)
        await self._s3.upload_drawing(
            key=s3_key,
            fileobj=file.file,
            content_type=self.KMZ_CONTENT_TYPE,
            sha256=content_hash.hexdigest(),
        )

        s3_url = HttpUrl(
            self.build_cloudfront_url(self._settings.aws_cloudfront_domain, drawing_id)
        )

        logger.info(
            "Drawing created: id=%s, size=%d, sha256=%s",
            drawing_id,
            size,
            content_hash.hexdigest(),
        )

        return DrawingsCreateResponse(
            id=drawing_id,
            admin_id=admin_id,
            s3_url=s3_url,
        )

    async def get_drawing(self, drawing_id: uuid.UUID) -> tuple[AsyncIterator[bytes], str]:
        """Verify existence and return a stream for the KMZ drawing.

        Performs a head request first so that DrawingNotFoundError is raised
        before the response stream starts.

        Args:
            drawing_id: The UUID of the drawing to retrieve.

        Returns:
            A tuple of (async byte iterator, s3_key).

        Raises:
            DrawingNotFoundError: If no drawing exists with the given identifier.
            S3Error: If the S3 read operation fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        # Verify the drawing exists before streaming, so that DrawingNotFoundError
        # is raised before the response starts and can be caught by the exception handler.
        await self._s3.head_drawing(s3_key)

        return self._s3.get_drawing(s3_key), s3_key


async def get_drawings_service(
    s3: S3ServiceDep,
    settings: SettingsDep,
) -> DrawingsService:
    """FastAPI dependency that provides a configured DrawingsService instance."""
    return DrawingsService(s3=s3, settings=settings)


DrawingsServiceDep = Annotated[DrawingsService, Depends(get_drawings_service)]
