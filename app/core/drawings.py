"""Drawings business logic — validation, S3 storage, URL construction."""

import hashlib
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request, UploadFile
from pydantic import HttpUrl

from app.core.exceptions import AdminIdMismatchError, DigestMismatchError
from app.core.s3 import S3Service, S3ServiceDep
from app.core.validation import validate_kmz
from app.schemas.drawings import DrawingsCreateResponse, DrawingsUpdateResponse

logger = logging.getLogger(__name__)


class DrawingsService:
    """Business logic for KMZ drawing creation, retrieval, and update."""

    S3_KEY_PREFIX = "drawings"
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"

    def __init__(self, s3: S3Service) -> None:
        self._s3 = s3

    @staticmethod
    def build_s3_key(drawing_id: uuid.UUID) -> str:
        """Build the S3 object key for a drawing identifier.

        Args:
            drawing_id: The UUID of the drawing.

        Returns:
            The S3 key in the format "drawings/{uuid}.kmz".

        """
        return f"{DrawingsService.S3_KEY_PREFIX}/{drawing_id}.kmz"

    async def _validate_digest(self, file: UploadFile, sha256: str) -> int:
        """Compute the file digest, verify it against the client value, and return its size.

        Reads the spooled upload in chunks so the content is never fully held
        in memory. Leaves the pointer at EOF (which also gives the size), then
        resets it to the start so the caller can stream the file to S3.

        Args:
            file: The KMZ file uploaded as multipart/form-data.
            sha256: The SHA-256 hex digest of the file, computed by the client
                before any network transfer.

        Returns:
            The size of the file in bytes.

        Raises:
            DigestMismatchError: If the client-provided SHA-256 does not match
                the received content.

        """
        # Starlette types UploadFile.file as BinaryIO, but it is always a
        # SpooledTemporaryFile, which provides the readinto() that file_digest() needs.
        computed_digest = hashlib.file_digest(file.file, "sha256").hexdigest()  # ty: ignore[invalid-argument-type]
        size = file.file.tell()
        await file.seek(0)  # reset so upload_fileobj reads from the start

        # Hex digests are compared case-insensitively: hexdigest() emits lowercase, but
        # clients may send uppercase.
        if not hmac.compare_digest(computed_digest, sha256.lower()):
            logger.warning(
                "SHA-256 digest mismatch: expected=%s, computed=%s", sha256, computed_digest
            )
            raise DigestMismatchError

        return size

    async def create_drawing(
        self, file: UploadFile, request: Request, sha256: str
    ) -> DrawingsCreateResponse:
        """Validate, hash, upload a KMZ drawing and return its metadata.

        Args:
            file: The KMZ file uploaded as multipart/form-data.
            request: The incoming request, used to build the access URL from
                the same domain the client used to reach the service.
            sha256: The SHA-256 hex digest of the file, computed by the client
                before any network transfer. It is verified against the
                received content (case-insensitively) and stored as S3 metadata.

        Returns:
            A response containing the drawing ID, admin ID placeholder,
            and the access URL where the file can be retrieved.

        Raises:
            InvalidKMZError: If the uploaded file is not a valid ZIP archive.
            DigestMismatchError: If the client-provided SHA-256 does not match
                the received content.
            S3Error: If the S3 upload operation fails.

        """
        await validate_kmz(file)
        size = await self._validate_digest(file, sha256)

        drawing_id = uuid.uuid4()
        admin_id = uuid.uuid4()

        now = datetime.now(UTC).isoformat()
        s3_key = self.build_s3_key(drawing_id)
        await self._s3.upload_drawing(
            key=s3_key,
            fileobj=file.file,
            content_type=self.KMZ_CONTENT_TYPE,
            metadata={
                "sha256": sha256.lower(),
                "admin-id": str(admin_id),
                "created-at": now,
                "modified-at": now,
            },
        )

        # Build the access URL from the request's own domain so the service
        # stays agnostic of any CDN/infrastructure in front of it
        s3_url = HttpUrl(str(request.url_for("get_drawing", drawing_id=drawing_id)))

        logger.info(
            "Drawing created: id=%s, size=%d, sha256=%s",
            drawing_id,
            size,
            sha256,
        )

        return DrawingsCreateResponse(
            id=drawing_id,
            admin_id=admin_id,
            s3_url=s3_url,
        )

    async def update_drawing(
        self,
        drawing_id: uuid.UUID,
        admin_id: uuid.UUID,
        file: UploadFile,
        request: Request,
        sha256: str,
    ) -> DrawingsUpdateResponse:
        """Replace an existing KMZ drawing, preserving admin identity and timestamps.

        Validates the admin_id against the stored metadata, verifies the new
        content, then overwrites the object at the same S3 key. If the content
        is unchanged, the upload is skipped entirely.

        Args:
            drawing_id: The UUID of the drawing to update.
            admin_id: Admin identifier that must match the stored drawing.
            file: The KMZ file uploaded as multipart/form-data.
            request: The incoming request, used to build the access URL from
                the same domain the client used to reach the service.
            sha256: The SHA-256 hex digest of the file, computed by the client
                before any network transfer. Verified against the received
                content (case-insensitively) and stored as S3 metadata.

        Returns:
            A response containing the drawing ID, admin ID, access URL, and
            creation/update timestamps.

        Raises:
            DrawingNotFoundError: If no drawing exists with the given identifier.
            AdminIdMismatchError: If the admin_id does not match the stored one.
            InvalidKMZError: If the uploaded file is not a valid ZIP archive.
            DigestMismatchError: If the client-provided SHA-256 does not match
                the received content.
            S3Error: If the S3 upload operation fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        # Head the object first so a missing drawing surfaces as a 404 before
        # any expensive validation happens
        existing = await self._s3.head_drawing(s3_key)

        if existing.get("admin-id") != str(admin_id):
            logger.warning("admin_id mismatch for drawing %s", drawing_id)
            raise AdminIdMismatchError

        await validate_kmz(file)
        size = await self._validate_digest(file, sha256)

        # Content unchanged: short-circuit with a 200 and no re-upload. The
        # comparison is case-insensitive because the stored digest is lowercase.
        if existing.get("sha256", "").lower() == sha256.lower():
            logger.info(
                "Drawing unchanged: id=%s, size=%d, sha256=%s",
                drawing_id,
                size,
                sha256.lower(),
            )
            return self._build_update_response(drawing_id, admin_id, request, existing)

        now = datetime.now(UTC).isoformat()
        metadata = {
            "sha256": sha256.lower(),
            "admin-id": str(admin_id),
            "created-at": existing["created-at"],
            "modified-at": now,
        }
        await self._s3.upload_drawing(s3_key, file.file, self.KMZ_CONTENT_TYPE, metadata)

        logger.info(
            "Drawing updated: id=%s, size=%d, sha256=%s",
            drawing_id,
            size,
            sha256.lower(),
        )

        return self._build_update_response(drawing_id, admin_id, request, metadata)

    def _build_update_response(
        self,
        drawing_id: uuid.UUID,
        admin_id: uuid.UUID,
        request: Request,
        metadata: dict[str, str],
    ) -> DrawingsUpdateResponse:
        """Build the update response from S3 metadata.

        Args:
            drawing_id: The UUID of the drawing.
            admin_id: The admin identifier of the drawing.
            request: The incoming request, used to build the access URL from
                the same domain the client used to reach the service.
            metadata: S3 metadata (sha256, admin-id, created-at, modified-at).

        Returns:
            A response containing the drawing ID, admin ID, access URL, and
            creation/update timestamps.

        """
        s3_url = HttpUrl(str(request.url_for("get_drawing", drawing_id=drawing_id)))
        return DrawingsUpdateResponse(
            id=drawing_id,
            admin_id=admin_id,
            s3_url=s3_url,
            created_at=datetime.fromisoformat(metadata["created-at"]),
            modified_at=datetime.fromisoformat(metadata["modified-at"]),
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

    async def delete_drawing(self, drawing_id: uuid.UUID, admin_id: uuid.UUID) -> None:
        """Delete a KMZ drawing from S3.

        Verifies the drawing exists and the admin_id matches the stored metadata
        before deleting the object. The deletion is permanent, the only way to
        recover it is through S3 versioning.

        Args:
            drawing_id: The UUID of the drawing to delete.
            admin_id: Admin identifier that must match the stored drawing.

        Raises:
            DrawingNotFoundError: If no drawing exists with the given identifier.
            AdminIdMismatchError: If the admin_id does not match the stored one.
            S3Error: If the S3 delete operation fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        # Head the object first so a missing drawing surfaces as a 404 before
        # the delete is attempted
        existing = await self._s3.head_drawing(s3_key)

        if existing.get("admin-id") != str(admin_id):
            logger.warning("admin_id mismatch for drawing %s", drawing_id)
            raise AdminIdMismatchError

        await self._s3.delete_drawing(s3_key)

        logger.info("Drawing deleted: id=%s", drawing_id)


async def get_drawings_service(
    s3: S3ServiceDep,
) -> DrawingsService:
    """FastAPI dependency that provides a configured DrawingsService instance."""
    return DrawingsService(s3=s3)


DrawingsServiceDep = Annotated[DrawingsService, Depends(get_drawings_service)]
