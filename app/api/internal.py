from fastapi import APIRouter

from app.schemas.checker import Checker
from app.version import __version__

INTERNAL_TAG = "Internal"

router = APIRouter(tags=[INTERNAL_TAG])


@router.get("/checker", summary="Kubernetes Probe")
async def get_checker() -> Checker:
    """Simple checker endpoint to be used by kubernetes probes"""
    return Checker(success=True, message="OK", version=__version__)
