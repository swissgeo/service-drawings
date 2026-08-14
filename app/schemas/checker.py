"""Pydantic models for health check responses.

Defines the Checker model returned by the Kubernetes probe endpoint,
containing success status, message, and service version fields.
"""

from pydantic import BaseModel, Field


class Checker(BaseModel):
    """Health check response model for Kubernetes probes."""

    success: bool = Field(
        description="True when the probe is successful, false otherwise",
        examples=[True],
    )
    message: str = Field(
        description="Failure explanation in case of failure, otherwise OK",
        examples=["OK"],
    )
    version: str = Field(
        description="Version of the service",
        examples=["v0.1.0"],
    )
