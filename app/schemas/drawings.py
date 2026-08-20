"""Pydantic models for drawings API responses.

Defines the response schemas returned after successful KMZ drawing creation
and update, containing the drawing identifier, admin identifier, S3 URL, and
timestamps.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class DrawingsCreateResponse(BaseModel):
    """Response model returned after a successful drawing upload.

    Attributes:
        id: Unique drawing identifier (UUID4).
        admin_id: Placeholder admin identifier reserved for future auth (UUID4).
        s3_url: HTTP URL where the KMZ file can be accessed.

    """

    id: UUID
    admin_id: UUID
    s3_url: HttpUrl


class DrawingsUpdateResponse(DrawingsCreateResponse):
    """Response model returned after a successful drawing update.

    Extends the creation response with creation and last-update timestamps.

    Attributes:
        created_at: UTC ISO-8601 timestamp of the initial upload.
        modified_at: UTC ISO-8601 timestamp of the last update.

    """

    created_at: datetime = Field(
        description="Creation timestamp",
        examples=["2026-01-01T12:00:00+00:00"],
    )
    modified_at: datetime = Field(
        description="Last update timestamp",
        examples=["2026-01-01T12:00:00+00:00"],
    )
