"""Pydantic models for drawings API responses.

Defines the response schema returned after successful KMZ drawing creation,
containing the drawing identifier, admin identifier, and CloudFront URL.
"""

from uuid import UUID

from pydantic import BaseModel, HttpUrl


class DrawingsCreateResponse(BaseModel):
    """Response model returned after a successful drawing upload.

    Attributes:
        id: Unique drawing identifier (UUID4).
        admin_id: Placeholder admin identifier reserved for future auth (UUID4).
        s3_url: CloudFront URL where the KMZ file can be accessed.

    """

    id: UUID
    admin_id: UUID
    s3_url: HttpUrl
