"""WPS drawings API router.

Provides REST endpoints for creating, retrieving, and updating KMZ drawing
files stored in S3 and served through CloudFront. All routes are prefixed
with /api/wps/v1 per SWISSGEO API standards.
"""

import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import HttpUrl

from app.core.s3 import S3Service, get_s3_service
from app.core.validation import validate_kmz
from app.schemas.drawings import DrawingsCreateResponse
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wps/v1")

KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"
S3_KEY_PREFIX = "drawings"

S3ServiceDep = Annotated[S3Service, Depends(get_s3_service)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _build_s3_key(drawing_id: uuid.UUID) -> str:
    """Build the S3 object key for a drawing identifier.

    Args:
        drawing_id: The UUID of the drawing.

    Returns:
        The S3 key in the format "drawings/{uuid}.kmz".

    """
    return f"{S3_KEY_PREFIX}/{drawing_id}.kmz"


def _build_cloudfront_url(domain: str, drawing_id: uuid.UUID) -> str:
    """Build the CloudFront URL for accessing a drawing.

    Args:
        domain: The CloudFront domain hostname (without https:// prefix).
        drawing_id: The UUID of the drawing.

    Returns:
        The full HTTPS URL to the KMZ file on CloudFront.

    """
    return f"https://{domain}/{S3_KEY_PREFIX}/{drawing_id}.kmz"


@router.post("/drawings", status_code=201)
async def create_drawing(
    file: UploadFile,
    s3: S3ServiceDep,
    settings: SettingsDep,
) -> DrawingsCreateResponse:
    """Upload a KMZ drawing file to S3 and return its access URL.

    Validates the uploaded file as a valid ZIP archive within the configured
    size limit, computes its SHA-256 hash for integrity verification, generates
    unique drawing and admin identifiers, and stores the file in S3.

    Args:
        file: The KMZ file uploaded as multipart/form-data.
        s3: S3 service dependency for object storage operations.
        settings: Application settings dependency.

    Returns:
        A response containing the drawing ID, admin ID placeholder,
        and the CloudFront URL where the file can be accessed.

    Raises:
        InvalidKMZError: If the uploaded file is not a valid ZIP archive.
        KMZTooLargeError: If the file exceeds the maximum allowed size.
        S3Error: If the S3 upload operation fails.

    """
    content = await file.read()

    validate_kmz(content, max_size=settings.max_kmz_size_bytes)

    content_hash = hashlib.sha256(content).hexdigest()
    drawing_id = uuid.uuid4()
    admin_id = uuid.uuid4()

    s3_key = _build_s3_key(drawing_id)
    await s3.upload_kml(
        key=s3_key,
        data=content,
        content_type=KMZ_CONTENT_TYPE,
        sha256=content_hash,
    )

    s3_url = HttpUrl(_build_cloudfront_url(settings.aws_cloudfront_domain, drawing_id))

    logger.info(
        "Drawing created: id=%s, size=%d, sha256=%s",
        drawing_id,
        len(content),
        content_hash,
    )

    return DrawingsCreateResponse(
        id=drawing_id,
        admin_id=admin_id,
        s3_url=s3_url,
    )


@router.get("/drawings/{drawing_id}")
async def get_drawing(
    drawing_id: uuid.UUID,
    s3: S3ServiceDep,
) -> StreamingResponse:
    """Retrieve a KMZ drawing file by its identifier.

    Streams the KMZ binary content directly from S3 with the appropriate
    Content-Type and Content-Disposition headers for browser download.

    Args:
        drawing_id: The UUID of the drawing to retrieve.
        s3: S3 service dependency for object storage operations.

    Returns:
        A streaming response with the KMZ file binary content.

    Raises:
        DrawingNotFoundError: If no drawing exists with the given identifier.
        S3Error: If the S3 read operation fails.

    """
    s3_key = _build_s3_key(drawing_id)

    # Verify the drawing exists before streaming, so that DrawingNotFoundError
    # is raised before the response starts and can be caught by the exception handler.
    await s3.head_kml(s3_key)

    return StreamingResponse(
        s3.get_kml(s3_key),
        media_type=KMZ_CONTENT_TYPE,
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
