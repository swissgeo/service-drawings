"""WPS drawings API router.

Provides REST endpoints for creating, retrieving, updating, and deleting KMZ
drawing files stored in S3 and served through CloudFront, plus endpoints to
check a drawing_id/admin_id pair and to read a drawing's metadata. All routes
are prefixed with /api/wps/v1 per SWISSGEO API standards.

Routes that need the admin_id take it as a "Swissgeo-Admin-Id: <admin_id>"
header, which keeps it out of URLs, access logs and browser history while
letting check-auth be a plain GET. A custom header rather than
"Authorization: Bearer" avoids clashing with OAuth2-proxy/JWT or a later move
to user accounts. A missing or malformed
header answers 401, an unknown drawing_id 404, and a wrong admin_id for an
existing drawing 403.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.security import APIKeyHeader

from app.core.drawings import DrawingsService, DrawingsServiceDep
from app.core.exceptions import ADMIN_ID_HEADER, MissingCredentialsError
from app.core.s3 import CACHE_CONTROL_NO_STORE
from app.schemas.drawings import (
    DrawingsCreateResponse,
    DrawingsMetadataResponse,
    DrawingsUpdateResponse,
)
from app.schemas.errors import ErrorResponse
from app.settings import get_settings

logger = logging.getLogger(__name__)

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

# auto_error is off so that a missing header answers 401 through the service's
# own error handler, uniformly with a malformed one; the scheme is still
# declared in the OpenAPI spec.
admin_id_header = APIKeyHeader(
    name=ADMIN_ID_HEADER,
    auto_error=False,
    scheme_name="AdminId",
    description=(
        f"The admin_id returned when the drawing was created, sent in the {ADMIN_ID_HEADER} header."
    ),
)


async def get_admin_id(
    request: Request,
    header: Annotated[str | None, Depends(admin_id_header)],
) -> uuid.UUID:
    """Extract the admin_id from a "Swissgeo-Admin-Id: <admin_id>" header.

    Raises:
        MissingCredentialsError: If the header is absent, empty, or carries a
            value that is not a UUID.

    """
    # Logged to ease debugging client integrations; the value itself never is.
    if header is None:
        logger.info("Missing %s header on %s %s", ADMIN_ID_HEADER, request.method, request.url.path)
        raise MissingCredentialsError
    try:
        return uuid.UUID(header)
    except ValueError as e:
        logger.info(
            "%s header is not a UUID on %s %s", ADMIN_ID_HEADER, request.method, request.url.path
        )
        raise MissingCredentialsError from e


AdminIdAuth = Annotated[uuid.UUID, Depends(get_admin_id)]

AUTH_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
}


@router.post(
    "",
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
    "/{drawing_id}",
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
    The download filename is the one the client used at the last upload, so
    browsers save the drawing under its original name; drawings stored before
    that name was recorded fall back to "{drawing_id}.kmz".
    """
    stream, filename = await drawings.get_drawing(drawing_id)

    return StreamingResponse(
        stream,
        media_type=DrawingsService.KMZ_CONTENT_TYPE,
        headers={
            "Content-Disposition": DrawingsService.build_content_disposition(filename),
            "Cache-Control": CACHE_CONTROL_NO_STORE,
        },
    )


@router.put(
    "/{drawing_id}",
    response_model=DrawingsUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        **AUTH_RESPONSES,
        413: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def update_drawing(  # noqa: PLR0913, PLR0917
    request: Request,
    drawing_id: uuid.UUID,
    admin_id: AdminIdAuth,
    file: KmzFile,
    sha256: Sha256Form,
    drawings: DrawingsServiceDep,
) -> DrawingsUpdateResponse:
    """Update an existing KMZ drawing by overwriting it at the same S3 key.

    Only KMZ files are accepted. The admin_id is sent as "Swissgeo-Admin-Id:
    <admin_id>" and must match the stored drawing metadata, otherwise
    the request is rejected with 403. If the new content is identical to the
    stored one, the request succeeds without re-uploading. Returns the drawing
    ID, access token, access URL, and creation/update timestamps.
    """
    return await drawings.update_drawing(drawing_id, admin_id, file, request, sha256)


@router.get(
    "/{drawing_id}/check-auth",
    status_code=204,
    responses={
        **AUTH_RESPONSES,
        500: {"model": ErrorResponse},
    },
)
async def check_auth(
    drawing_id: uuid.UUID,
    admin_id: AdminIdAuth,
    drawings: DrawingsServiceDep,
) -> Response:
    """Check whether an admin_id grants write access to a drawing.

    Answers through the status code alone: 204 when the admin_id matches,
    403 when the drawing exists but the admin_id does not match, and 404 when
    the drawing does not exist. A client can therefore fall back to opening an
    existing drawing read-only after a 403 without a second request.

    The answer depends on the Swissgeo-Admin-Id header, so it is marked no-store:
    a cache keyed on the URL alone must never replay it to another caller.
    """
    await drawings.check_auth(drawing_id, admin_id)
    return Response(status_code=204, headers={"Cache-Control": CACHE_CONTROL_NO_STORE})


@router.get(
    "/{drawing_id}/metadata",
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_drawing_metadata(
    drawing_id: uuid.UUID,
    response: Response,
    drawings: DrawingsServiceDep,
) -> DrawingsMetadataResponse:
    """Retrieve a drawing's metadata without downloading its content.

    Returns the filename used at the last upload and the creation/update
    timestamps. Like the download itself, which already exposes the filename
    through Content-Disposition, it needs no admin_id, so a client can show a
    drawing's details in read-only mode too.

    Marked no-store because an update changes the answer.
    """
    response.headers["Cache-Control"] = CACHE_CONTROL_NO_STORE
    return await drawings.get_drawing_metadata(drawing_id)


@router.delete(
    "/{drawing_id}",
    status_code=204,
    responses={
        **AUTH_RESPONSES,
        500: {"model": ErrorResponse},
    },
)
async def delete_drawing(
    drawing_id: uuid.UUID,
    admin_id: AdminIdAuth,
    drawings: DrawingsServiceDep,
) -> Response:
    """Delete a KMZ drawing.

    The admin_id is sent as "Swissgeo-Admin-Id: <admin_id>" and must match
    the stored drawing metadata, otherwise the request is rejected with 403.
    The deletion is permanent.
    """
    await drawings.delete_drawing(drawing_id, admin_id)
    return Response(status_code=204)
