"""Internal API router for Kubernetes health probes.

Provides the /checker endpoint used by Kubernetes liveness and readiness
probes. All routes in this router are tagged as Internal and excluded from
the public OpenAPI specification.
"""

from fastapi import APIRouter

from app.schemas.checker import Checker
from app.version import __version__

INTERNAL_TAG = "Internal"

router = APIRouter(tags=[INTERNAL_TAG])


@router.get("/checker", summary="Kubernetes Probe")
async def get_checker() -> Checker:
    """Simple checker endpoint to be used by kubernetes probes."""
    return Checker(success=True, message="OK", version=__version__)
