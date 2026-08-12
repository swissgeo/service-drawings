"""WPS drawings API router.

Provides REST endpoints for creating, retrieving, and updating KMZ drawing
files stored in S3 and served through CloudFront. All routes are prefixed
with /api/wps/v1 per SWISSGEO API standards.
"""

import uuid

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.drawings import DrawingsService, DrawingsServiceDep
from app.schemas.drawings import DrawingsCreateResponse
from app.settings import get_settings

settings = get_settings()

router = APIRouter(prefix=settings.api_prefix)


@router.post("/drawings", status_code=201)
async def create_drawing(
    request: Request,
    file: UploadFile,
    drawings: DrawingsServiceDep,
) -> DrawingsCreateResponse:
    """Upload a KMZ drawing file to S3 and return its access URL.

    Delegates validation, hashing, ID generation, S3 upload, and URL
    construction to the DrawingsService.

    Args:
        request: The incoming request, used to build the access URL from the
            same domain the client used to reach the service.
        file: The KMZ file uploaded as multipart/form-data.
        drawings: DrawingsService dependency.

    Returns:
        A response containing the drawing ID, admin ID placeholder,
        and the access URL where the file can be retrieved.

    """
    return await drawings.create_drawing(file, request)


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


@router.put("/drawings/{drawing_id}")
async def update_drawing(drawing_id: uuid.UUID) -> JSONResponse:  # noqa: ARG001
    """Update an existing KMZ drawing (reserved for future implementation).

    This endpoint is not implemented in T1. It will support overwriting
    an existing KMZ file at the same S3 key in a future release.

    Args:
        drawing_id: The UUID of the drawing to update.

    Returns:
        501 Not Implemented with an explanatory message.

    """
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented"},
    )
