"""WPS drawings API router.

Provides REST endpoints for creating, retrieving, and updating KMZ drawing
files stored in S3 and served through CloudFront. All routes are prefixed
with /api/wps/v1 per SWISSGEO API standards.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.drawings import DrawingsService, DrawingsServiceDep
from app.schemas.drawings import DrawingsCreateResponse, DrawingsUpdateResponse
from app.schemas.errors import ErrorResponse
from app.settings import get_settings

settings = get_settings()

DRAWINGS_TAG = "Drawings"

router = APIRouter(prefix=settings.api_prefix, tags=[DRAWINGS_TAG])

Sha256Form = Annotated[
    str,
    Form(
        pattern=r"^[0-9a-fA-F]{64}$",
        description=(
            "SHA-256 hex digest of the KMZ file bytes (not of the multipart body), "
            "computed by the client before upload. Case-insensitive."
        ),
    ),
]

KmzFile = Annotated[
    UploadFile,
    File(description="The KMZ file to upload. Only KMZ files are accepted."),
]


@router.post(
    "/drawings",
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def create_drawing(
    request: Request,
    file: KmzFile,
    drawings: DrawingsServiceDep,
    sha256: Sha256Form,
) -> DrawingsCreateResponse:
    """Upload a KMZ drawing file to S3 and return its access URL.

    Only KMZ files are accepted. The client must provide the SHA-256 hex
    digest of the file bytes, which is verified against the received content
    before storing. Returns the drawing ID, admin ID, and the access URL.
    """
    return await drawings.create_drawing(file, request, sha256)


@router.get(
    "/drawings/{drawing_id}",
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_drawing(
    drawing_id: uuid.UUID,
    drawings: DrawingsServiceDep,
) -> StreamingResponse:
    """Retrieve a KMZ drawing file by its identifier.

    Streams the KMZ binary content directly from S3 with the appropriate
    Content-Type and Content-Disposition headers for an attachment download.
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
        500: {"model": ErrorResponse},
    },
)
async def update_drawing(  # noqa: PLR0913
    request: Request,
    drawing_id: uuid.UUID,
    admin_id: Annotated[
        uuid.UUID,
        Form(
            description="Admin identifier required to update the drawing",
            examples=["00000000-0000-0000-0000-000000000000"],
        ),
    ],
    file: KmzFile,
    sha256: Sha256Form,
    drawings: DrawingsServiceDep,
) -> DrawingsUpdateResponse:
    """Update an existing KMZ drawing by overwriting it at the same S3 key.

    Only KMZ files are accepted. The admin_id must match the stored drawing
    metadata, otherwise the request is rejected with 403. If the new content
    is identical to the stored one, the request succeeds without re-uploading.
    Returns the drawing ID, access token, access URL, and creation/update
    timestamps.
    """
    return await drawings.update_drawing(drawing_id, admin_id, file, request, sha256)
