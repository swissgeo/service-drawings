"""WPS drawings API router.

Provides REST endpoints for creating, retrieving, and updating KMZ drawing
files stored in S3 and served through CloudFront. All routes are prefixed
with /api/wps/v1 per SWISSGEO API standards.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.drawings import DrawingsService, DrawingsServiceDep
from app.schemas.drawings import DrawingsCreateResponse, DrawingsUpdateResponse
from app.schemas.errors import ErrorResponse
from app.settings import get_settings

settings = get_settings()

DRAWINGS_TAG = "Drawings"

router = APIRouter(prefix=settings.api_prefix, tags=[DRAWINGS_TAG])


@router.post("/drawings", status_code=201)
async def create_drawing(
    request: Request,
    file: UploadFile,
    drawings: DrawingsServiceDep,
    sha256: Annotated[
        str,
        Form(
            pattern=r"^[0-9a-fA-F]{64}$",
            description=(
                "SHA-256 hex digest of the KMZ file bytes (not of the multipart body), "
                "computed by the client before upload. Case-insensitive."
            ),
        ),
    ],
) -> DrawingsCreateResponse:
    """Upload a KMZ drawing file to S3 and return its access URL.

    Delegates validation, hashing, ID generation, S3 upload, and URL
    construction to the DrawingsService.

    Args:
        request: The incoming request, used to build the access URL from the
            same domain the client used to reach the service.
        file: The KMZ file uploaded as multipart/form-data.
        drawings: DrawingsService dependency.
        sha256: The SHA-256 hex digest of the file, computed by the client
            before any network transfer. Verified against the received content.

    Returns:
        A response containing the drawing ID, admin ID placeholder,
        and the access URL where the file can be retrieved.

    """
    return await drawings.create_drawing(file, request, sha256)


@router.get("/drawings/{drawing_id}")
async def get_drawing(
    drawing_id: uuid.UUID,
    drawings: DrawingsServiceDep,
) -> StreamingResponse:
    """Retrieve a KMZ drawing file by its identifier.

    Streams the KMZ binary content directly from S3 with the appropriate
    Content-Type and Content-Disposition headers for browser download.

    Args:
        drawing_id: The UUID of the drawing to retrieve.
        drawings: DrawingsService dependency.

    Returns:
        A streaming response with the KMZ file binary content.

    Raises:
        DrawingNotFoundError: If no drawing exists with the given identifier.
        S3Error: If the S3 read operation fails.

    """
    stream, _ = await drawings.get_drawing(drawing_id)

    return StreamingResponse(
        stream,
        media_type=DrawingsService.KMZ_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{drawing_id}.kmz"',
        },
    )


@router.put(
    "/drawings/{drawing_id}",
    response_model=DrawingsUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
    },
)
async def update_drawing(  # noqa: PLR0913
    request: Request,
    drawing_id: uuid.UUID,
    admin_id: Annotated[
        uuid.UUID,
        Query(
            description="Admin identifier required to update the drawing",
            examples=["00000000-0000-0000-0000-000000000000"],
        ),
    ],
    file: UploadFile,
    sha256: Annotated[str, Form(pattern=r"^[0-9a-fA-F]{64}$")],
    drawings: DrawingsServiceDep,
) -> DrawingsUpdateResponse:
    """Update an existing KMZ drawing by overwriting it at the same S3 key.

    Validates the admin_id against the stored metadata, verifies the new
    content, and replaces the file. If the content is unchanged, the request
    succeeds without re-uploading.

    Args:
        request: The incoming request, used to build the access URL from the
            same domain the client used to reach the service.
        drawing_id: The UUID of the drawing to update.
        admin_id: Admin identifier that must match the stored drawing.
        file: The KMZ file uploaded as multipart/form-data.
        sha256: The SHA-256 hex digest of the file, computed by the client
            before any network transfer. Verified against the received content.
        drawings: DrawingsService dependency.

    Returns:
        A response containing the drawing ID, admin ID, access URL, and
        creation/update timestamps.

    Raises:
        DrawingNotFoundError: If no drawing exists with the given identifier.
        AdminIdMismatchError: If the admin_id does not match the stored one.
        InvalidKMZError: If the uploaded file is not a valid ZIP archive.
        DigestMismatchError: If the client-provided SHA-256 does not match
            the received content.

    """
    return await drawings.update_drawing(drawing_id, admin_id, file, request, sha256)
