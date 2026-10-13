"""Internal API router for Kubernetes health probes.

Provides the /checker endpoint used by Kubernetes liveness and readiness
probes. All routes in this router are tagged as Internal and excluded from
the public OpenAPI specification.
"""

from fastapi import APIRouter, Response, status

from app.core.syntheticz_service import SyntheticzServiceDep
from app.schemas.checker import Checker
from app.schemas.syntheticz import Syntheticz, SyntheticzStatus
from app.version import __version__

INTERNAL_TAG = "Internal"

router = APIRouter(tags=[INTERNAL_TAG])


@router.get("/checker", summary="Kubernetes Probe")
async def get_checker() -> Checker:
    """Simple checker endpoint to be used by kubernetes probes."""
    return Checker(success=True, message="OK", version=__version__)


@router.get("/checker/ready", summary="Kubernetes Readiness Probe")
async def get_checker_ready() -> Checker:
    """Readiness probe to be used by kubernetes probes.

    Only verifies that the process is up and accepting traffic. External
    dependency checks (S3...) belong to synthetic checks, not readiness.
    """
    return Checker(success=True, message="OK", version=__version__)


@router.get(
    "/syntheticz",
    summary="Synthetic Check",
    status_code=200,
    responses={500: {"model": Syntheticz}},
)
async def get_syntheticz(
    response: Response,
    syntheticz: SyntheticzServiceDep,
) -> Syntheticz:
    """Synthetic check endpoint to be used by kubernetes probes."""
    response.headers["Cache-Control"] = "no-store"

    health = await syntheticz.check()

    if health.status != SyntheticzStatus.UP:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return health
