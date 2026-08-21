"""Shared error response schema for the drawings API."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response body.

    Attributes:
        detail: Human-readable description of the error.

    """

    detail: str
