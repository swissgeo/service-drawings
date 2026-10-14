import logging
from typing import Annotated

from opentelemetry import trace

from fastapi import Depends

from app.core.s3 import S3Service, S3ServiceDep
from app.schemas.syntheticz import (
    Syntheticz,
    SyntheticzExternalSystem,
    SyntheticzServiceDetails,
    SyntheticzStatus,
)
from app.version import __version__

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(__name__)


class SyntheticzService:
    """Service for managing syntheticz checks."""

    def __init__(self, s3: S3Service) -> None:
        self._s3 = s3

    async def check(self) -> Syntheticz:
        """Check the status of the service."""
        s3 = "s3"
        syntheticz = Syntheticz(
            service=SyntheticzServiceDetails(name="service-drawings", version=__version__),
            status=SyntheticzStatus.UP,
            external_systems={s3: SyntheticzExternalSystem(status=SyntheticzStatus.UP)},
        )

        with tracer.start_as_current_span("syntheticz-s3") as span:
            span.set_attribute("syntheticz", True)  # noqa: FBT003 ignore boolean-positional-value-in-call here
            try:
                ok = await self._s3.check_bucket()
            except Exception:
                # We explicitely use broad exception here, as we want to be sure that any
                # exception is returning DOWN status, even if we don't know the exact reason.
                logger.exception("Failed to access s3 bucket")
                syntheticz.status = SyntheticzStatus.DOWN
                syntheticz.external_systems[s3].status = SyntheticzStatus.DOWN
            else:
                if not ok:
                    syntheticz.status = SyntheticzStatus.DOWN
                    syntheticz.external_systems[s3].status = SyntheticzStatus.DOWN

        return syntheticz


# FastAPI dependency injection function and definitions
async def get_syntheticz_service(
    s3: S3ServiceDep,
) -> SyntheticzService:
    """Get the syntheticz service FastAPI dependency."""
    return SyntheticzService(s3)


SyntheticzServiceDep = Annotated[SyntheticzService, Depends(get_syntheticz_service)]
