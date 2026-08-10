"""Internal API router for Kubernetes health probes.

Provides the /checker endpoint used by Kubernetes liveness and readiness
probes. All routes in this router are tagged as Internal and excluded from
the public OpenAPI specification.
"""

from fastapi import APIRouter, HTTPException

from app.core.s3 import S3ServiceDep
from app.schemas.checker import Checker
from app.version import __version__

INTERNAL_TAG = "Internal"

router = APIRouter(tags=[INTERNAL_TAG])


@router.get("/checker", summary="Kubernetes Probe")
async def get_checker() -> Checker:
    """Simple checker endpoint to be used by kubernetes probes."""
    return Checker(success=True, message="OK", version=__version__)


@router.get("/checker/ready", summary="Kubernetes Readiness Probe")
async def get_checker_ready(
    s3: S3ServiceDep,
) -> Checker:
    """Readiness probe that verifies S3 connectivity.

    Performs a head_bucket request against the configured S3 bucket
    to confirm the service can reach its storage backend. Used by
    Kubernetes readiness probes to determine if the pod should
    receive traffic.

    Args:
        s3: S3 service dependency for connectivity verification.

    Returns:
        Checker with success=True if S3 is reachable.

    Raises:
        HTTPException: 503 if S3 is unreachable.

    """
    if await s3.check_bucket():
        return Checker(success=True, message="OK", version=__version__)
    raise HTTPException(
        status_code=503,
        detail="S3 unavailable",
    )
