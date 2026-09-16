"""Drawings business logic — validation, S3 storage, URL construction."""

import hashlib
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import Depends, Request, UploadFile
from pydantic import HttpUrl

from app.core.exceptions import (
    DigestMismatchError,
    DrawingNotFoundError,
    S3Error,
)
from app.core.s3 import S3Service, S3ServiceDep
from app.core.validation import validate_kmz
from app.schemas.drawings import (
    DrawingsCreateResponse,
    DrawingsMetadataResponse,
    DrawingsUpdateResponse,
)

logger = logging.getLogger(__name__)


class DrawingsService:
    """Business logic for KMZ drawing creation, retrieval, and update."""

    S3_KEY_PREFIX = "drawings"
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"
    # S3 caps user metadata at 2 KB in total, and percent-encoding can triple the
    # length of a non-ASCII name, so the raw name is capped well below that.
    MAX_FILENAME_LENGTH = 255

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

    @staticmethod
    def encode_filename(filename: str | None, drawing_id: uuid.UUID) -> str:
        """Normalise a client-supplied filename into an S3-metadata-safe value.

        S3 user metadata travels in HTTP headers and must therefore be US-ASCII,
        so the name is percent-encoded; decode_filename() reverses this. Any
        directory component is stripped first, since the value is echoed back to
        clients and must never be mistaken for a path.

        Args:
            filename: The filename reported by the client, which may be absent.
            drawing_id: The UUID of the drawing, used to build a fallback name
                when the client sends no usable filename.

        Returns:
            The percent-encoded basename, or the percent-encoded fallback
            "{drawing_id}.kmz" when the client sent no filename or one that is
            purely path syntax.

        """
        # PureWindowsPath treats both forward and backward slashes as separators, so a
        # single call strips directory components from POSIX and Windows paths alike.
        name = PureWindowsPath(filename).name if filename else ""
        # A name that is purely path syntax ("/", "..") leaves nothing usable behind.
        if name in {"", ".", ".."}:
            name = f"{drawing_id}.kmz"
        return quote(name[: DrawingsService.MAX_FILENAME_LENGTH], safe="")

    @staticmethod
    def decode_filename(value: str | None) -> str | None:
        """Decode a filename previously stored by encode_filename().

        Args:
            value: The percent-encoded metadata value, absent for drawings
                stored before the original filename was recorded.

        Returns:
            The original filename, or None when the metadata does not carry one.

        """
        return unquote(value) if value else None

    @staticmethod
    def build_content_disposition(filename: str) -> str:
        """Build the attachment Content-Disposition header for a download filename.

        RFC 6266 carries the name twice: a bare "filename" restricted to
        printable US-ASCII, and a "filename*" holding the exact UTF-8 name.
        Clients that understand "filename*" use it and ignore the other, so the
        ASCII form is only a fallback and may safely lose characters.

        Args:
            filename: The name the browser should save the file under.

        Returns:
            The full header value, e.g.
            `attachment; filename="Z_rich.kmz"; filename*=UTF-8''Z%C3%BCrich.kmz`.

        """
        # A quote or backslash would terminate or escape the quoted-string, and
        # non-ASCII characters cannot appear in it at all, so the fallback keeps
        # only what a client can read back verbatim.
        ascii_name = "".join(
            c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in filename
        )
        ext_value = "UTF-8''" + quote(filename, safe="")
        return f'attachment; filename="{ascii_name}"; filename*={ext_value}'

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
            file: The KMZ file uploaded as multipart/form-data. Its filename is
                recorded as S3 metadata so it can be read back later through
                get_drawing_metadata().
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
                "original-filename": self.encode_filename(file.filename, drawing_id),
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
        is unchanged, the upload is skipped entirely, which also means a
        rename that carries identical bytes leaves the recorded original
        filename untouched.

        Args:
            drawing_id: The UUID of the drawing to update.
            admin_id: Admin identifier that must match the stored drawing.
            file: The KMZ file uploaded as multipart/form-data. Its filename
                replaces the recorded original filename.
            request: The incoming request, used to build the access URL from
                the same domain the client used to reach the service.
            sha256: The SHA-256 hex digest of the file, computed by the client
                before any network transfer. Verified against the received
                content (case-insensitively) and stored as S3 metadata.

        Returns:
            A response containing the drawing ID, admin ID, access URL, and
            creation/update timestamps.

        Raises:
            DrawingNotFoundError: If no drawing exists with the given
                identifier, or if the admin_id does not match the stored one.
                The two are deliberately not distinguished, for the same reason
                as in is_valid(): a distinct error for a wrong admin_id would
                confirm that a drawing exists, letting this endpoint be used to
                probe which identifiers are in use.
            InvalidKMZError: If the uploaded file is not a valid ZIP archive.
            DigestMismatchError: If the client-provided SHA-256 does not match
                the received content.
            S3Error: If the S3 upload operation fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        # Head the object first so a missing drawing surfaces as a 404 before
        # any expensive validation happens
        existing = await self._s3.head_drawing(s3_key)

        # Constant-time, like is_valid(): this is an oracle on a secret value.
        if not hmac.compare_digest(existing.get("admin-id", ""), str(admin_id)):
            logger.warning("admin_id mismatch for drawing %s", drawing_id)
            raise DrawingNotFoundError

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
            "original-filename": self.encode_filename(file.filename, drawing_id),
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
        """Verify existence and return a stream and download name for the KMZ drawing.

        Performs a head request first so that DrawingNotFoundError is raised
        before the response stream starts, which also yields the filename
        recorded at the last upload without a second S3 call.

        Args:
            drawing_id: The UUID of the drawing to retrieve.

        Returns:
            A tuple of (async byte iterator, download filename). The filename is
            the one the client used at the last upload, falling back to
            "{drawing_id}.kmz" for drawings stored before it was recorded.

        Raises:
            DrawingNotFoundError: If no drawing exists with the given identifier.
            S3Error: If the S3 read operation fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        # Verify the drawing exists before streaming, so that DrawingNotFoundError
        # is raised before the response starts and can be caught by the exception handler.
        existing = await self._s3.head_drawing(s3_key)
        filename = self.decode_filename(existing.get("original-filename")) or f"{drawing_id}.kmz"

        return self._s3.get_drawing(s3_key), filename

    async def is_valid(self, drawing_id: uuid.UUID, admin_id: uuid.UUID) -> bool:
        """Report whether a drawing_id/admin_id pair identifies an existing drawing.

        A missing drawing and a wrong admin_id are deliberately not
        distinguished: both return False, so the check cannot be used to probe
        which drawing identifiers exist. The admin_id comparison is
        constant-time because this method is an oracle on a secret value.

        Args:
            drawing_id: The UUID of the drawing to check.
            admin_id: Admin identifier to check against the stored drawing.

        Returns:
            True if the drawing exists and the admin_id matches, False otherwise.

        Raises:
            S3Error: If the S3 head request fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        try:
            existing = await self._s3.head_drawing(s3_key)
        except DrawingNotFoundError:
            logger.info("Validity check on unknown drawing: id=%s", drawing_id)
            return False

        is_valid = hmac.compare_digest(existing.get("admin-id", ""), str(admin_id))
        if not is_valid:
            logger.info("Validity check failed for drawing %s: admin_id mismatch", drawing_id)

        return is_valid

    async def get_drawing_metadata(
        self, drawing_id: uuid.UUID, admin_id: uuid.UUID
    ) -> DrawingsMetadataResponse:
        """Return a drawing's stored metadata without transferring its content.

        Args:
            drawing_id: The UUID of the drawing to describe.
            admin_id: Admin identifier that must match the stored drawing.

        Returns:
            A response containing the drawing ID, the original upload filename,
            and the creation/update timestamps. The filename is None for
            drawings stored before it was recorded.

        Raises:
            DrawingNotFoundError: If no drawing exists with the given
                identifier, or if the admin_id does not match the stored one.
                The two are deliberately not distinguished, for the same reason
                as in is_valid(): a distinct error for a wrong admin_id would
                confirm that a drawing exists, letting this endpoint be used to
                probe which identifiers are in use.
            S3Error: If the S3 head request fails or the stored timestamps are
                missing or unparsable.

        """
        s3_key = self.build_s3_key(drawing_id)

        existing = await self._s3.head_drawing(s3_key)

        # Constant-time, like is_valid(): this is an oracle on a secret value.
        if not hmac.compare_digest(existing.get("admin-id", ""), str(admin_id)):
            logger.warning("admin_id mismatch for drawing %s", drawing_id)
            raise DrawingNotFoundError

        # Unlike the update path, which reads timestamps it has just written,
        # this is a read of arbitrarily old objects, so corrupt or pre-existing
        # metadata is surfaced as a 500 rather than an unhandled KeyError.
        try:
            created_at = datetime.fromisoformat(existing["created-at"])
            modified_at = datetime.fromisoformat(existing["modified-at"])
        except (KeyError, ValueError) as e:
            logger.exception("Unusable timestamps in metadata for drawing %s", drawing_id)
            raise S3Error(f"Unusable timestamps in metadata for drawing {drawing_id}") from e

        return DrawingsMetadataResponse(
            id=drawing_id,
            original_filename=self.decode_filename(existing.get("original-filename")),
            created_at=created_at,
            modified_at=modified_at,
        )

    async def delete_drawing(self, drawing_id: uuid.UUID, admin_id: uuid.UUID) -> None:
        """Delete a KMZ drawing from S3.

        Verifies the drawing exists and the admin_id matches the stored metadata
        before deleting the object. The deletion is permanent, the only way to
        recover it is through S3 versioning.

        Args:
            drawing_id: The UUID of the drawing to delete.
            admin_id: Admin identifier that must match the stored drawing.

        Raises:
            DrawingNotFoundError: If no drawing exists with the given
                identifier, or if the admin_id does not match the stored one.
                The two are deliberately not distinguished, for the same reason
                as in is_valid(): a distinct error for a wrong admin_id would
                confirm that a drawing exists, letting this endpoint be used to
                probe which identifiers are in use.
            S3Error: If the S3 delete operation fails.

        """
        s3_key = self.build_s3_key(drawing_id)

        # Head the object first so a missing drawing surfaces as a 404 before
        # the delete is attempted
        existing = await self._s3.head_drawing(s3_key)

        # Constant-time, like is_valid(): this is an oracle on a secret value.
        if not hmac.compare_digest(existing.get("admin-id", ""), str(admin_id)):
            logger.warning("admin_id mismatch for drawing %s", drawing_id)
            raise DrawingNotFoundError

        await self._s3.delete_drawing(s3_key)

        logger.info("Drawing deleted: id=%s", drawing_id)


async def get_drawings_service(
    s3: S3ServiceDep,
) -> DrawingsService:
    """FastAPI dependency that provides a configured DrawingsService instance."""
    return DrawingsService(s3=s3)


DrawingsServiceDep = Annotated[DrawingsService, Depends(get_drawings_service)]
